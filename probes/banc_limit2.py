"""Ce que `banc_limit.py` n'a pas couvert — deuxieme passe, 2026-09-12, carte blanche de Victor.

La premiere passe (T1-T5) a exerce la file profonde, une LECTURE pendant un rafraichissement,
l'inactivite et deux instances. Restent les cas qui peuvent encore faire tomber `limit=1` :

  T10 rafraichissement rapide (`force_slow_refresh=False`), celui que Home Assistant fait le plus
      souvent, N = 6 et 12, avec le tas libre de l'appareil avant et apres chaque rafale.
  T6  une ECRITURE pendant un rafraichissement — l'action reelle d'un utilisateur. PUT `wusts`
      avec `dspon` et `brght` a leur valeur actuelle : aucun effet observable, relu a la fin.
  T8  l'annulation — Home Assistant annule les taches trop longues. Avec une seule place dans le
      pool, une requete annulee qui ne la rendrait pas bloquerait tout le reste.
  T9  la reprise apres erreur EN VRAI — un client tiers sans limite provoque des coupures ; la
      session recreee garde-t-elle `limit=1`, et repond-elle ensuite ?
  T7  l'endurance — 8 min par paquet : rafraichissement toutes les 5 s (complet toutes les
      30 s), actions au hasard pendant les rafraichissements (lecture ou PUT neutre). Une place
      jamais rendue ne se voit qu'a la longue.

ECRITURE : uniquement `wusts` {dspon, brght} a l'identique, lu avant, relu apres. Rien d'autre.
Reprend les outils de `banc_limit.py`. A lancer avec `.venv/bin/python3`.
"""
import asyncio
import json
import random
import signal
import ssl
import statistics
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from banc_limit import (PAQUETS, REESSAIS, Releve, arreter_capture, charger, chrono, fermer,
                        limite_session, trouver)
from somneo_probe import get

ENDURANCE_S = 8 * 60
_stop = False


def _arret(signum, frame):
    global _stop
    _stop = True


def tas(ip):
    return ((get(ip, 0, "mem").get("body") or {}).get("heap_free"))


def affichage(ip):
    b = get(ip, 1, "wusts").get("body") or {}
    return b.get("dspon"), b.get("brght")


def remettre_affichage(ip, dspon, brght):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    req = urllib.request.Request(f"https://{ip}/di/v1/products/1/wusts", method="PUT",
                                 data=json.dumps({"dspon": dspon, "brght": brght}).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15, context=ctx).read()
    except Exception as exc:
        print("remise de l'affichage en echec :", type(exc).__name__, flush=True)


async def instance(ip, paquet):
    pysomneo, _ = charger(paquet)
    s = pysomneo.Somneo(ip)
    await chrono(s.fetch_data(force_slow_refresh=True))       # amorce, hors mesure
    await asyncio.sleep(1)
    REESSAIS.remise_a_zero()
    return s


def quantiles(v):
    if not v:
        return None
    v = sorted(v)
    return {"med": round(statistics.median(v), 2), "p95": round(v[int(0.95 * (len(v) - 1))], 2),
            "max": round(v[-1], 2)}


async def t10(ip, rel, paquet, n, serie):
    avant = tas(ip)
    s = await instance(ip, paquet)
    s._last_sensor_fetch = 0              # sinon fetch_data() saute les capteurs, trop recents
    t0 = time.monotonic()
    appels = await asyncio.wait_for(
        asyncio.gather(*[chrono(s.fetch_data()) for _ in range(n)]), 240)
    duree = round(time.monotonic() - t0, 2)
    bilan = REESSAIS.bilan()
    await fermer(s)
    await asyncio.sleep(2)
    apres = tas(ip)
    rec = {"volet": "T10", "paquet": paquet, "taches": n, "serie": serie, "duree_s": duree,
           "ok": sum(a["ok"] for a in appels), "total": n,
           "erreurs": [a for a in appels if not a["ok"]], "tas_avant": avant, "tas_apres": apres,
           **bilan}
    rel.ecrire(**rec)
    print(f"T10 {paquet:<12} N={n:<2} s{serie} {rec['ok']}/{n} {duree:5.1f} s  tas {avant}->{apres}"
          f"  absorbes={bilan['echecs_absorbes'] or 0} abandons={bilan['abandons']}", flush=True)


async def t6(ip, rel, paquet, essai, d0, b0):
    s = await instance(ip, paquet)
    retard = random.uniform(0.05, 0.4)

    async def action():
        await asyncio.sleep(retard)
        return await chrono(s._client.put("wusts", {"dspon": d0, "brght": b0}))

    rafraichi, ecrit = await asyncio.gather(chrono(s.fetch_data(force_slow_refresh=True)),
                                            action())
    bilan = REESSAIS.bilan()
    await fermer(s)
    rel.ecrire(volet="T6", paquet=paquet, essai=essai, retard_s=round(retard, 3),
               rafraichissement=rafraichi, ecriture=ecrit, **bilan)
    print(f"T6  {paquet:<12} #{essai:<2} refresh {'ok' if rafraichi['ok'] else 'ECHEC'} "
          f"{rafraichi['s']:5.2f} s  PUT {'ok' if ecrit['ok'] else 'ECHEC ' + str(ecrit.get('err'))} "
          f"{ecrit['s']:5.2f} s  absorbes={bilan['echecs_absorbes'] or 0}", flush=True)


async def t8(ip, rel, paquet, essai):
    s = await instance(ip, paquet)
    taches = [asyncio.create_task(s.fetch_data(force_slow_refresh=True)) for _ in range(3)]
    delai = random.uniform(0.05, 0.6)
    await asyncio.sleep(delai)
    for t in taches:
        t.cancel()
    res = await asyncio.gather(*taches, return_exceptions=True)
    annulees = sum(isinstance(x, asyncio.CancelledError) for x in res)
    try:
        suite = await asyncio.wait_for(chrono(s.fetch_data(force_slow_refresh=True)), 60)
    except asyncio.TimeoutError:
        suite = {"ok": False, "s": 60.0, "err": "plafond 60 s"}
    bilan = REESSAIS.bilan()
    limite = limite_session(s)
    await fermer(s)
    rel.ecrire(volet="T8", paquet=paquet, essai=essai, delai_s=round(delai, 3),
               annulees=annulees, suite=suite, limite=limite, **bilan)
    print(f"T8  {paquet:<12} #{essai:<2} annulees {annulees}/3 a {delai:.2f} s -> suite "
          f"{'ok' if suite['ok'] else 'ECHEC ' + str(suite.get('err'))} {suite['s']:5.2f} s  "
          f"absorbes={bilan['echecs_absorbes'] or 0}", flush=True)


async def t9(ip, rel, paquet, tour):
    import aiohttp
    s = await instance(ip, paquet)
    fin = time.monotonic() + 20

    async def tiers():
        """Un client SANS limite, deux requetes a la fois : il provoque les coupures."""
        envoyees = 0
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False)) as cs:
            async def une():
                try:
                    async with cs.get(f"https://{ip}/di/v1/products/1/wusrd",
                                      timeout=aiohttp.ClientTimeout(total=10)) as r:
                        await r.read()
                except Exception:
                    pass
            while time.monotonic() < fin:
                await asyncio.gather(une(), une())
                envoyees += 2
                await asyncio.sleep(0.1)
        return envoyees

    async def principal():
        res = []
        while time.monotonic() < fin:
            res.append(await chrono(s.fetch_data(force_slow_refresh=True)))
        return res

    envoyees, appels = await asyncio.gather(tiers(), principal())
    bilan = REESSAIS.bilan()
    limite_perturbe = limite_session(s)
    await asyncio.sleep(3)
    suite = await chrono(s.fetch_data(force_slow_refresh=True))
    limite_apres = limite_session(s)
    await fermer(s)
    rel.ecrire(volet="T9", paquet=paquet, tour=tour, tiers_envoyees=envoyees,
               appels_ok=sum(a["ok"] for a in appels), appels=len(appels), suite=suite,
               limite_perturbe=limite_perturbe, limite_apres=limite_apres, **bilan)
    print(f"T9  {paquet:<12} t{tour} sous perturbation {sum(a['ok'] for a in appels)}/{len(appels)}"
          f"  resets={bilan['resets']}  limit={limite_perturbe}  -> suite "
          f"{'ok' if suite['ok'] else 'ECHEC'} {suite['s']:5.2f} s limit={limite_apres}", flush=True)


async def t7(ip, rel, paquet, d0, b0):
    s = await instance(ip, paquet)
    t0 = time.monotonic()
    fin = t0 + ENDURANCE_S
    rafr, actions, en_vol = [], [], set()
    prochaine = t0 + random.uniform(10, 25)
    i, minute = 0, 1

    async def lancer(genre):
        await asyncio.sleep(random.uniform(0.05, 0.3))        # tombe pendant le rafraichissement
        coro = (s._client.put("wusts", {"dspon": d0, "brght": b0}) if genre == "put"
                else s._client.get_sensor_data())
        r = await chrono(coro)
        r["genre"] = genre
        actions.append(r)

    while time.monotonic() < fin and not _stop:
        debut = time.monotonic()
        if debut >= prochaine:
            t = asyncio.create_task(lancer("put" if len(actions) % 2 else "get"))
            en_vol.add(t)
            t.add_done_callback(en_vol.discard)
            prochaine = debut + random.uniform(10, 25)
        complet = i % 6 == 0
        s._last_sensor_fetch = 0
        r = await chrono(s.fetch_data(force_slow_refresh=complet))
        r["complet"] = complet
        rafr.append(r)
        i += 1
        if debut - t0 >= 60 * minute:
            print(f"T7  {paquet:<12} {minute} min : {sum(x['ok'] for x in rafr)}/{len(rafr)} "
                  f"rafraichissements, {sum(x['ok'] for x in actions)}/{len(actions)} actions, "
                  f"absorbes={REESSAIS.bilan()['echecs_absorbes'] or 0}", flush=True)
            minute += 1
        await asyncio.sleep(max(0.0, 5 - (time.monotonic() - debut)))
    await asyncio.gather(*en_vol, return_exceptions=True)
    bilan = REESSAIS.bilan()
    limite = limite_session(s)
    await fermer(s)
    rec = {"volet": "T7", "paquet": paquet, "duree_s": round(time.monotonic() - t0),
           "rafraichissements_ok": sum(x["ok"] for x in rafr), "rafraichissements": len(rafr),
           "rapide": quantiles([x["s"] for x in rafr if not x["complet"] and x["ok"]]),
           "complet": quantiles([x["s"] for x in rafr if x["complet"] and x["ok"]]),
           "actions_ok": sum(x["ok"] for x in actions), "actions": len(actions),
           "actions_s": quantiles([x["s"] for x in actions if x["ok"]]),
           "echecs": [x for x in rafr + actions if not x["ok"]], "limite": limite, **bilan}
    rel.ecrire(**rec)
    print(f"T7  {paquet:<12} FIN {rec['rafraichissements_ok']}/{rec['rafraichissements']} "
          f"rafraichissements (rapide {rec['rapide']}, complet {rec['complet']}), "
          f"{rec['actions_ok']}/{rec['actions']} actions {rec['actions_s']}, "
          f"absorbes={bilan['echecs_absorbes'] or 0} abandons={bilan['abandons']} "
          f"limit={limite}", flush=True)


async def main():
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    rel = Releve(time.strftime("banc-limit2-%Y%m%dT%H%M%S.jsonl"))
    pids, vivants = arreter_capture()
    rel.ecrire(volet="debut", capture_arretee=pids, capture_vivante=vivants)
    if vivants:
        rel.ecrire(volet="fin", abandon="capture vivante")
        return 1
    ip = trouver()
    if not ip:
        rel.ecrire(volet="fin", abandon="Somneo introuvable")
        return 1
    d0, b0 = affichage(ip)
    rel.ecrire(volet="affichage_initial", dspon=d0, brght=b0)
    print(f"affichage initial : dspon={d0} brght={b0}", flush=True)
    if d0 is None or b0 is None:
        rel.ecrire(volet="fin", abandon="affichage illisible : pas d'ecriture a l'aveugle")
        return 1

    etapes = []
    for n in (6, 12):
        for serie in (1, 2, 3):
            for p in (PAQUETS if serie % 2 else PAQUETS[::-1]):
                etapes.append(lambda p=p, n=n, serie=serie: t10(ip, rel, p, n, serie))
    for essai in range(1, 11):
        for p in (PAQUETS if essai % 2 else PAQUETS[::-1]):
            etapes.append(lambda p=p, essai=essai: t6(ip, rel, p, essai, d0, b0))
    for essai in range(1, 9):
        for p in (PAQUETS if essai % 2 else PAQUETS[::-1]):
            etapes.append(lambda p=p, essai=essai: t8(ip, rel, p, essai))
    for tour in (1, 2, 3):
        for p in PAQUETS:
            etapes.append(lambda p=p, tour=tour: t9(ip, rel, p, tour))
    for p in PAQUETS[::-1]:                                   # le correctif d'abord
        etapes.append(lambda p=p: t7(ip, rel, p, d0, b0))

    try:
        for etape in etapes:
            if _stop:
                rel.ecrire(volet="interruption")
                break
            await etape()
            await asyncio.sleep(3)
    finally:
        await asyncio.sleep(2)
        d1, b1 = affichage(ip)
        if (d1, b1) != (d0, b0):
            remettre_affichage(ip, d0, b0)
            await asyncio.sleep(1)
            d1, b1 = affichage(ip)
        conforme = (d1, b1) == (d0, b0)
        rel.ecrire(volet="affichage_final", dspon=d1, brght=b1, conforme=conforme)
        print(f"affichage final : dspon={d1} brght={b1}  "
              f"{'conforme' if conforme else 'NON CONFORME — A VERIFIER'}", flush=True)
        rel.ecrire(volet="fin")
        print(f"Releve -> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
