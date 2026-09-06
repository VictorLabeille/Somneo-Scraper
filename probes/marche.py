"""Ou se situe la marche : combien de requetes simultanees l'appareil tolere-t-il ?

L'experience precedente a mesure 1 (21/21), 3 (7/21) et 7 (8/21). Le palier 2 manque, et
c'est lui qui decide du correctif a proposer en amont : une seule requete en vol, ou deux.

Trois series par palier : une serie unique peut etre chanceuse, et une affirmation sur un
seul tirage n'est pas un fait. Connexions reutilisees (keep-alive) dans tous les cas, c'est
la configuration reelle d'une bibliotheque. Lecture seule.
"""
import http.client
import json
import ssl
import sys
import threading
import time

from somneo_probe import discover, heap

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
CHEMIN = "/di/v1/products/1/wusrd"
PAR_SERIE = 21
SERIES = 3
REPOS = 15.0


def _worker(ip, n, sortie, verrou):
    conn = http.client.HTTPSConnection(ip, timeout=20, context=CTX)
    for _ in range(n):
        debut = time.monotonic()
        try:
            conn.request("GET", CHEMIN, headers={"Connection": "keep-alive"})
            resp = conn.getresponse()
            resp.read()
            rec = {"ok": resp.status == 200, "status": resp.status}
        except Exception as exc:
            rec = {"ok": False, "error": type(exc).__name__}
            try:
                conn.close()
            except Exception:
                pass
            conn = http.client.HTTPSConnection(ip, timeout=20, context=CTX)
        rec["ms"] = round((time.monotonic() - debut) * 1000, 1)
        with verrou:
            sortie.append(rec)
    conn.close()


def serie(ip, concurrence):
    sortie, verrou = [], threading.Lock()
    fils = [threading.Thread(target=_worker,
                             args=(ip, PAR_SERIE // concurrence, sortie, verrou))
            for _ in range(concurrence)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    ko = [r for r in sortie if not r["ok"]]
    from collections import Counter
    return {"envoyees": len(sortie), "echouees": len(ko),
            "erreurs": dict(Counter(r["error"] for r in ko))}


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    libre, taille, _ = heap(ip)
    print(f"tas au depart : {libre} / {taille}\n", flush=True)

    resultats = {}
    for conc in (1, 2, 3):
        series = []
        for i in range(SERIES):
            r = serie(ip, conc)
            series.append(r)
            print(f"  {conc} en vol, serie {i+1}/{SERIES} : "
                  f"{r['envoyees'] - r['echouees']}/{r['envoyees']} ok"
                  + (f"  {r['erreurs']}" if r["erreurs"] else ""), flush=True)
            time.sleep(REPOS)
        total = sum(s["envoyees"] for s in series)
        echecs = sum(s["echouees"] for s in series)
        resultats[conc] = {"series": series, "total": total, "echecs": echecs}
        print(f"  -> palier {conc} : {total - echecs}/{total} sur {SERIES} series\n",
              flush=True)

    nom = f"marche-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(resultats, fh, ensure_ascii=False, indent=2)
    print(f"Releve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
