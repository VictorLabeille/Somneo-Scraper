"""Escalade de cadence — quelle frequence de relevé l'appareil tient-il ?

Question ouverte du cadrage du backend : « une mesure par minute au minimum, plus fin si
l'appareil le supporte », un palier n'etant retenu que s'il tient sans un seul `500 Timeout`.
Ce test fait le premier tri, a raison de 45 minutes par palier — pas les 24 h du critere final,
mais assez pour eliminer ce qui casse tout de suite.

Une seule requete en vol, toujours : on mesure la cadence, pas la concurrence.
"""
import json
import sys
import time

sys.path.insert(0, ".")
from somneo_probe import discover, get

PALIERS = [60, 30, 15, 5]      # secondes entre deux cycles
DUREE = 45 * 60                # par palier
PORTS = [(1, "wusrd"), (1, "wusts")]
ESPACEMENT = 0.2


def palier(ip, periode):
    debut = time.time()
    total = echecs = 0
    erreurs = {}
    latences = []
    heap_debut = (get(ip, 0, "mem").get("body") or {}).get("heap_free")
    while time.time() - debut < DUREE:
        cycle = time.time()
        for produit, port in PORTS:
            r = get(ip, produit, port)
            total += 1
            latences.append(r["ms"])
            if not r["ok"]:
                echecs += 1
                cle = str(r.get("error") or r.get("status"))[:40]
                erreurs[cle] = erreurs.get(cle, 0) + 1
            time.sleep(ESPACEMENT)
        reste = periode - (time.time() - cycle)
        if reste > 0:
            time.sleep(reste)
    heap_fin = (get(ip, 0, "mem").get("body") or {}).get("heap_free")
    latences.sort()
    return {
        "periode_s": periode, "requetes": total, "echecs": echecs,
        "erreurs": erreurs,
        "ms_median": latences[len(latences) // 2] if latences else None,
        "ms_max": latences[-1] if latences else None,
        "heap_debut": heap_debut, "heap_fin": heap_fin,
    }


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    resultats = []
    for periode in PALIERS:
        print(f"palier {periode}s — {DUREE//60} min…", flush=True)
        r = palier(ip, periode)
        resultats.append(r)
        print(f"  {r['requetes'] - r['echecs']}/{r['requetes']} ok"
              f" | med {r['ms_median']:.0f} max {r['ms_max']:.0f} ms"
              f" | tas {r['heap_debut']} -> {r['heap_fin']}"
              + (f" | erreurs {r['erreurs']}" if r["erreurs"] else ""), flush=True)
        if r["echecs"]:
            print("  >> ce palier echoue : on ne descend pas plus bas.", flush=True)
            break
        time.sleep(30)
    with open(f"cadence-{time.strftime('%Y%m%dT%H%M%S')}.json", "w", encoding="utf-8") as fh:
        json.dump(resultats, fh, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
