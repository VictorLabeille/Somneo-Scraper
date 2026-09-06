"""Mesure du seuil de saturation du Somneo — reponse mesuree a l'issue #8 de pysomneo.

Hypothese a verifier : l'appareil ne tombe pas en `500 Timeout` a cause de l'objet
`requests.Session` (conjecture du rapporteur en 2022), mais parce qu'il n'a pas la memoire
de servir des requetes rapprochees — le port `mem` annonce ~25 ko de tas libre.

Protocole : paliers de rafales de plus en plus longues, SANS pause entre les requetes d'un
palier, avec releve du tas libre avant et apres chaque palier et 15 s de repos entre paliers.
Lecture seule. Aucune ecriture, aucun effet persistant sur l'appareil.
"""
import json
import sys
import time

from somneo_probe import discover, get, heap

PALIERS = [1, 3, 5, 10, 20]
REPOS = 15.0


def palier(ip, n):
    """Envoie n GET `wusrd` d'affilee, sans aucune pause. Renvoie le compte rendu."""
    before, size, _ = heap(ip)
    time.sleep(0.5)
    recs = [get(ip, 1, "wusrd", timeout=10) for _ in range(n)]
    time.sleep(1.0)
    after, _, _ = heap(ip)
    ok = [r for r in recs if r["ok"]]
    ko = [r for r in recs if not r["ok"]]
    return {
        "rafale": n,
        "reussies": len(ok),
        "echouees": len(ko),
        "latence_ms": [r["ms"] for r in recs],
        "erreurs": sorted({r.get("error", "") for r in ko} - {""}),
        "heap_avant": before,
        "heap_apres": after,
        "heap_size": size,
        "records": recs,
    }


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    print(f"Somneo decouvert par SSDP\n")

    resultats = []
    for n in PALIERS:
        r = palier(ip, n)
        resultats.append(r)
        lat = r["latence_ms"]
        print(
            f"rafale de {n:>2} -> {r['reussies']} ok / {r['echouees']} echec"
            f" | latence min {min(lat):.0f} med {sorted(lat)[len(lat)//2]:.0f}"
            f" max {max(lat):.0f} ms"
            f" | tas {r['heap_avant']} -> {r['heap_apres']}"
        )
        if r["erreurs"]:
            print(f"             erreurs : {r['erreurs']}")
        if r["echouees"]:
            print(f"  >> seuil atteint a {n} requetes enchainees, on s'arrete la.")
            break
        time.sleep(REPOS)

    out = f"burst-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({"paliers": resultats, "heap_size": resultats[0]["heap_size"]}, fh,
                  ensure_ascii=False, indent=2)
    print(f"\nReleve complet -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
