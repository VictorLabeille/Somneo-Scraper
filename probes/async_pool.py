"""La branche `ai-improvements` executee contre l'appareil — elle ne l'avait jamais ete.

Tout ce qu'on sait de la refonte async etait de l'INFERENCE : `docs/somneo-api.md` §7 le dit
explicitement. Or c'est la branche que vise la PR (6.0.0b0, poussee le 2026-09-03), et proposer
un correctif sur une branche jamais mesuree contredirait l'exigence posee le 2026-09-06.

Ce que le code de la branche fait aujourd'hui (`api.py`, `_get_session`) :

    connector = aiohttp.TCPConnector(ssl=False)      # aucun `limit`

Defauts aiohttp : `limit=100`, `limit_per_host=0` (illimite). La branche async autorise donc
**cent** connexions simultanees la ou `master` en autorise cinq — vers un appareil qui n'en
sert qu'une. La protection accidentelle qu'offrait l'API synchrone (un fil, un appel a la fois)
disparait avec `asyncio.gather`, qui est precisement la maniere dont on ecrit du code async.

Trois conditions, meme charge :
  A. connecteur par defaut          — l'existant de la branche
  B. `TCPConnector(limit=1)`        — le correctif propose
  C. `limit=1` + `force_close=True` — au cas ou la reutilisation poserait probleme

La charge est `asyncio.gather` de N appels a `fetch_data(force_slow_refresh=True)` sur une
instance partagee : la forme exacte qu'aura le composant Home Assistant en 6.0.0.

Lecture seule : `fetch_data()` n'ecrit rien.
"""
import asyncio
import json
import sys
import time

sys.path.insert(0, ".")

# La branche async est deposee dans `asyncpkg/pysomneo/`, sans etre installee : on ne veut pas
# ecraser le `pysomneo` synchrone du venv, dont dependent les autres sondes. Ce chemin passe
# AVANT les site-packages, donc c'est bien la branche qui est importee — verifie au demarrage.
sys.path.insert(0, "asyncpkg")

from somneo_probe import discover

PALIERS = [2, 3, 6]
SERIES = 3

_get_session_origine = None


def decouvrir(essais=4):
    for n in range(1, essais + 1):
        ip = discover(timeout=6)
        if ip:
            return ip
        print(f"  SSDP sans reponse (essai {n}/{essais})")
        time.sleep(3)
    return None


async def mesure(ip, fabrique_connecteur, taches):
    """N `fetch_data` concurrents sur une instance partagee. Renvoie (ok, echecs, duree, types)."""
    import aiohttp
    from pysomneo import Somneo

    poser_connecteur(fabrique_connecteur)
    s = Somneo(ip)

    try:
        await s.fetch_data(force_slow_refresh=True)   # amorce
    except Exception as exc:
        print(f"     amorce en echec : {type(exc).__name__}")
    await asyncio.sleep(1)

    t0 = time.monotonic()
    resultats = await asyncio.gather(
        *[s.fetch_data(force_slow_refresh=True) for _ in range(taches)],
        return_exceptions=True,
    )
    duree = time.monotonic() - t0

    ok, types = 0, {}
    for r in resultats:
        if isinstance(r, BaseException):
            n = type(r).__name__
            types[n] = types.get(n, 0) + 1
        else:
            ok += 1
    await _fermer(s)
    return ok, len(resultats), duree, types


def poser_connecteur(fabrique):
    """Impose notre connecteur a TOUTE session que la branche creera.

    Substituer l'attribut `_session` ne suffit pas, et c'est un piege qui fausserait la mesure
    en silence : sur une erreur de connexion, `SomneoSession.request` appelle `_reset_session()`,
    qui ferme la session et la remet a `None` ; `_get_session()` en recree alors une avec
    `TCPConnector(ssl=False)` — le connecteur par defaut de la branche. La contrainte testee
    disparaitrait donc des la premiere erreur, c'est-a-dire exactement quand elle compte.

    On remplace donc la methode, pas l'attribut. `fabrique=None` restaure le comportement
    d'origine de la branche.
    """
    import aiohttp
    import pysomneo.api as api

    if fabrique is None:
        api.SomneoSession._get_session = _get_session_origine
        return

    async def _get_session(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=self._timeout,      # le timeout de la branche, inchange
                connector=fabrique(),
            )
        return self._session

    api.SomneoSession._get_session = _get_session


async def _fermer(somneo):
    wrapper = somneo._client.session
    if wrapper._session is not None and not wrapper._session.closed:
        await wrapper._session.close()
    wrapper._session = None


async def main_async():
    import aiohttp

    ip = decouvrir()
    if not ip:
        print("Somneo introuvable en SSDP — sonde abandonnee.")
        return 1
    import pysomneo
    import pysomneo.api as api

    global _get_session_origine
    _get_session_origine = api.SomneoSession._get_session

    chemin = pysomneo.__file__ or ""
    if "asyncpkg" not in chemin:
        print(f"ERREUR : c'est le pysomneo synchrone qui est importe ({chemin}).")
        print("La sonde mesurerait la mauvaise branche — abandon.")
        return 1
    print("Branche ai-improvements (6.0.0b0) executee contre l'appareil.")
    print(f"  paquet : {chemin}")
    print(f"  aiohttp {aiohttp.__version__}\n")

    conditions = [
        # `None` = on laisse la branche faire ce qu'elle fait, sans rien substituer.
        ("A. TCPConnector(ssl=False) — l'existant de la branche", None),
        ("B. TCPConnector(ssl=False, limit=1) — le correctif",
         lambda: aiohttp.TCPConnector(ssl=False, limit=1)),
        ("C. limit=1 + force_close=True",
         lambda: aiohttp.TCPConnector(ssl=False, limit=1, force_close=True)),
    ]

    releve = []
    for libelle, fabrique in conditions:
        print("=" * 70)
        print(libelle)
        print("=" * 70)
        for taches in PALIERS:
            lignes = []
            for serie in range(SERIES):
                ok, total, duree, types = await mesure(ip, fabrique, taches)
                lignes.append({"ok": ok, "total": total, "duree_s": round(duree, 1),
                               "types": types})
                await asyncio.sleep(4)
            oks = sum(l["ok"] for l in lignes)
            totaux = sum(l["total"] for l in lignes)
            durees = sorted(l["duree_s"] for l in lignes)
            types = {}
            for l in lignes:
                for k, v in l["types"].items():
                    types[k] = types.get(k, 0) + v
            print(f"   {taches} taches : {oks:>2}/{totaux}   durees {durees}   "
                  f"{types or 'aucune erreur'}")
            releve.append({"condition": libelle, "taches": taches, "ok": oks,
                           "total": totaux, "durees": durees, "types": types})
        print()

    print("=" * 70)
    print("Bilan")
    print("=" * 70)
    for r in releve:
        print(f"   {r['taches']} taches  {r['ok']:>2}/{r['total']}  "
              f"mediane {r['durees'][1]:>6.1f} s   {r['condition'][:34]}")

    nom = time.strftime("async-pool-%Y%m%dT%H%M%S.json")
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(releve, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
