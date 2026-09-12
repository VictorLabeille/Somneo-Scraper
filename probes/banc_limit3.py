"""T9b — forcer la reprise apres erreur EN VRAI, que T9 n'a pas su provoquer (2026-09-12).

T9 (`banc_limit2.py`) perturbait avec un client tiers a deux requetes : l'appareil ralentit,
mais la connexion deja ouverte de `pysomneo` n'est jamais coupee — zero `_reset_session()` des
deux cotes. Le chemin de reprise n'etait donc couvert que par construction et par introspection
(T3), pas en conditions reelles.

Ici, deux leviers ensemble :
  - 16 s d'inactivite d'abord : au-dela du `keepalive_timeout` d'aiohttp (15 s), la connexion
    inactive est fermee, et la requete suivante doit en OUVRIR une neuve ;
  - au meme moment, un client tiers sans limite ouvre 8 puis 16 connexions simultanees.
Une ouverture qui echoue leve `ClientConnectorError`, que `SomneoSession` traite en
`_reset_session()`. On compte les resets, et on relit `limit` apres coup.

Si aucun reset n'apparait, le dire : ce banc ne sait pas le provoquer.
Lecture seule : que des GET. Reprend les outils de `banc_limit.py`.
"""
import asyncio
import signal
import sys
import time

sys.path.insert(0, ".")
from banc_limit import (PAQUETS, REESSAIS, Releve, arreter_capture, charger, chrono, fermer,
                        limite_session, trouver)

TOURS = (8, 8, 16, 16)
_stop = False


def _arret(signum, frame):
    global _stop
    _stop = True


async def tour(ip, rel, paquet, n, i):
    import aiohttp
    pysomneo, _ = charger(paquet)
    s = pysomneo.Somneo(ip)
    await chrono(s.fetch_data(force_slow_refresh=True))       # amorce : ouvre la connexion
    await asyncio.sleep(16)                                    # > keepalive_timeout : elle se ferme
    REESSAIS.remise_a_zero()
    fin = time.monotonic() + 15

    async def tiers():
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as cs:
            async def boucle():
                while time.monotonic() < fin:
                    try:
                        async with cs.get(f"https://{ip}/di/v1/products/1/wusrd",
                                          timeout=aiohttp.ClientTimeout(total=8)) as r:
                            await r.read()
                    except Exception:
                        await asyncio.sleep(0.05)
            await asyncio.gather(*[boucle() for _ in range(n)])

    async def principal():
        await asyncio.sleep(1)                                 # le tiers occupe deja l'appareil
        res = []
        while time.monotonic() < fin:
            res.append(await chrono(s.fetch_data(force_slow_refresh=True)))
        return res

    _, appels = await asyncio.gather(tiers(), principal())
    bilan = REESSAIS.bilan()
    limite_perturbe = limite_session(s)
    await asyncio.sleep(3)
    suite = await chrono(s.fetch_data(force_slow_refresh=True))
    limite_apres = limite_session(s)
    await fermer(s)
    rel.ecrire(volet="T9b", paquet=paquet, tour=i, tiers_connexions=n,
               appels_ok=sum(a["ok"] for a in appels), appels=len(appels),
               erreurs=[a for a in appels if not a["ok"]], suite=suite,
               limite_perturbe=limite_perturbe, limite_apres=limite_apres, **bilan)
    print(f"T9b {paquet:<12} t{i} tiers={n:<2} {sum(a['ok'] for a in appels)}/{len(appels)} "
          f"resets={bilan['resets']} absorbes={bilan['echecs_absorbes'] or 0} "
          f"abandons={bilan['abandons']} limit pendant={limite_perturbe} -> suite "
          f"{'ok' if suite['ok'] else 'ECHEC'} {suite['s']:5.2f} s limit={limite_apres}", flush=True)


async def main():
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    rel = Releve(time.strftime("banc-limit3-%Y%m%dT%H%M%S.jsonl"))
    pids, vivants = arreter_capture()
    rel.ecrire(volet="debut", capture_arretee=pids, capture_vivante=vivants)
    if vivants:
        rel.ecrire(volet="fin", abandon="capture vivante")
        return 1
    ip = trouver()
    if not ip:
        rel.ecrire(volet="fin", abandon="Somneo introuvable")
        return 1
    try:
        for i, n in enumerate(TOURS, 1):
            for p in PAQUETS[::-1]:
                if _stop:
                    rel.ecrire(volet="interruption")
                    return 0
                await tour(ip, rel, p, n, i)
                await asyncio.sleep(5)
    finally:
        rel.ecrire(volet="fin")
        print(f"Releve -> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
