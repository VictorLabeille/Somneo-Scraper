"""Reproduire le `ReadTimeout` exact de l'issue #8 — le dernier trou de la demonstration.

Le rapporteur de 2022 voit :

    requests.exceptions.ReadTimeout: HTTPSConnectionPool(host=..., port=443):
    Read timed out. (read timeout=20)

Nous voyons, nous, des `SSLEOFError` et des `RemoteDisconnected` — jamais son erreur. C'est la
reserve honnete du brouillon : « I didn't reproduce your exact error tho. » La mesure du pool
(`pool_serialise.py`) a montre pourquoi elle est a portee : a trois fils, la pire requete monte
a **17,5 s** pour un `read_timeout` de **20,0 s** — le defaut de `pysomneo`, et le chiffre meme
de la trace du rapporteur. Il manque deux secondes et demie.

Cette sonde monte la charge jusqu'a les franchir. Elle utilise **un seul objet `Somneo`
partage** par N fils appelant `fetch_data()` : c'est la forme exacte du composant Home
Assistant, ou le coordinator et les actions utilisateur tapent sur la meme instance.

Si un `ReadTimeout` sort, la chaine est complete : meme cause, meme symptome, meme message.
Sinon, la reserve du brouillon reste — et c'est un resultat aussi, a dire tel quel.

Lecture seule : `fetch_data()` n'ecrit rien.
"""
import json
import sys
import threading
import time
import traceback

sys.path.insert(0, ".")

import urllib3

from somneo_probe import discover

urllib3.disable_warnings()

PALIERS = [2, 3, 4, 6]
TOURS = 3          # appels a fetch_data() par fil et par palier
REPOS = 8          # secondes entre deux paliers, pour rendre l'appareil a lui-meme


def decouvrir(essais=4):
    """SSDP rate parfois du premier coup — un M-SEARCH perdu suffit."""
    for n in range(1, essais + 1):
        ip = discover(timeout=6)
        if ip:
            return ip
        print(f"  SSDP sans reponse (essai {n}/{essais})")
        time.sleep(3)
    return None


def palier(ip, fils):
    """`fils` fils appellent `fetch_data()` sur UNE instance partagee, comme le fait HA."""
    from pysomneo import Somneo

    print("=" * 70)
    print(f"{fils} fils sur une instance partagee, {TOURS} appels chacun")
    print("=" * 70)

    s = Somneo(ip)
    try:
        s.fetch_data()          # amorce : cache rempli, connexion etablie
    except Exception as exc:
        print(f"  amorce en echec : {type(exc).__name__}")
    time.sleep(1)

    resultats = []
    verrou = threading.Lock()
    depart = threading.Barrier(fils)

    def travail(n):
        depart.wait()
        for tour in range(TOURS):
            t0 = time.monotonic()
            try:
                # `force_slow_refresh=True` est INDISPENSABLE : sans lui, `fetch_data()` se
                # court-circuite sur ses compteurs `_last_sensor_fetch` / `_last_slow_fetch`
                # et rend la main en 0 ms sans toucher au reseau. Une premiere version de
                # cette sonde a ainsi mesure 45 appels a 0 ms et conclu a tort qu'il ne se
                # passait rien.
                s.fetch_data(force_slow_refresh=True)
                issue = {"ok": True}
            except Exception as exc:
                issue = {"ok": False, "type": type(exc).__name__,
                         "message": str(exc)[:200]}
                # La trace complete du premier ReadTimeout : c'est la piece a montrer.
                if "ReadTimeout" in type(exc).__name__:
                    issue["trace"] = traceback.format_exc()[-1500:]
            issue["ms"] = round((time.monotonic() - t0) * 1000, 1)
            issue["fil"], issue["tour"] = n, tour
            with verrou:
                resultats.append(issue)

    threads = [threading.Thread(target=travail, args=(n,)) for n in range(fils)]
    t0 = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    duree = time.monotonic() - t0

    ok = sum(1 for r in resultats if r["ok"])
    types = {}
    for r in resultats:
        if not r["ok"]:
            types[r["type"]] = types.get(r["type"], 0) + 1
    lat = sorted(r["ms"] for r in resultats)
    readtimeouts = [r for r in resultats if not r["ok"] and "ReadTimeout" in r["type"]]

    print(f"   reussite : {ok}/{len(resultats)}   en {duree:.1f} s")
    print(f"   latence  : med {lat[len(lat) // 2]:.0f} ms   max {lat[-1]:.0f} ms")
    print(f"   erreurs  : {types or 'aucune'}")
    if readtimeouts:
        print(f"   >>> ReadTimeout REPRODUIT : {len(readtimeouts)} fois <<<")
        print(f"   >>> {readtimeouts[0]['message'][:160]}")
    print()
    return {"fils": fils, "ok": ok, "total": len(resultats), "duree_s": round(duree, 1),
            "ms_median": lat[len(lat) // 2], "ms_max": lat[-1], "types": types,
            "readtimeout": len(readtimeouts),
            "trace": readtimeouts[0].get("trace") if readtimeouts else None}


def main():
    ip = decouvrir()
    if not ip:
        print("Somneo introuvable en SSDP — sonde abandonnee.")
        return 1
    print(f"Somneo decouvert. read_timeout par defaut de pysomneo : 20,0 s\n")

    releve = []
    for fils in PALIERS:
        releve.append(palier(ip, fils))
        time.sleep(REPOS)

    print("=" * 70)
    print("Bilan")
    print("=" * 70)
    for r in releve:
        marque = f"  <<< {r['readtimeout']} ReadTimeout" if r["readtimeout"] else ""
        print(f"   {r['fils']} fils : {r['ok']:>2}/{r['total']}  max {r['ms_max']:>8.0f} ms  "
              f"{r['types'] or 'aucune erreur'}{marque}")

    total_rt = sum(r["readtimeout"] for r in releve)
    print()
    if total_rt:
        print(f"   ReadTimeout reproduit {total_rt} fois — l'erreur de l'issue #8 est atteinte.")
    else:
        print("   Aucun ReadTimeout : la reserve du brouillon reste vraie, et se dit telle quelle.")

    nom = time.strftime("readtimeout-%Y%m%dT%H%M%S.json")
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(releve, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
