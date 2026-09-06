"""Issue #8 — reproduction dans la pile du rapporteur : `requests.Session`.

L'experience precedente (stdlib http.client) a montre que la concurrence casse et que la
reutilisation de connexion, elle, aide. Reste a verifier que cela se manifeste bien en
`ReadTimeout` cote `requests` — c'est le symptome decrit dans l'issue. Si oui, le diagnostic
tient dans la pile ou le bug a ete rapporte, et pas seulement dans la notre.

Adaptateur configure comme `SomneoSession` de pysomneo 5.0.6 : pool de 5, 3 relances,
timeouts (5, 20). Lecture seule.
"""
import json
import sys
import threading
import time

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

sys.path.insert(0, ".")
from somneo_probe import discover

urllib3.disable_warnings()

N = 21
TIMEOUT = (5.0, 20.0)


def session_comme_pysomneo():
    s = requests.Session()
    s.verify = False
    adapter = HTTPAdapter(
        pool_connections=5, pool_maxsize=5,
        max_retries=Retry(total=3, backoff_factor=2.0,
                          status_forcelist=[500, 502, 503, 504],
                          allowed_methods=["GET", "PUT", "POST"]),
        pool_block=False,
    )
    s.mount("https://", adapter)
    return s


def campagne(url, concurrence, session_partagee):
    """N requetes reparties sur `concurrence` fils. Session partagee ou une par fil."""
    sortie, verrou = [], threading.Lock()
    partagee = session_comme_pysomneo() if session_partagee else None

    def worker(part):
        sess = partagee or session_comme_pysomneo()
        for _ in range(part):
            debut = time.monotonic()
            try:
                r = sess.get(url, timeout=TIMEOUT)
                rec = {"ok": r.status_code == 200, "status": r.status_code}
            except Exception as exc:
                rec = {"ok": False, "error": type(exc).__name__}
            rec["ms"] = round((time.monotonic() - debut) * 1000, 1)
            with verrou:
                sortie.append(rec)

    fils = [threading.Thread(target=worker, args=(N // concurrence,))
            for _ in range(concurrence)]
    debut = time.monotonic()
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    duree = time.monotonic() - debut

    lat = sorted(r["ms"] for r in sortie)
    ko = [r for r in sortie if not r["ok"]]
    from collections import Counter
    return {
        "concurrence": concurrence, "session_partagee": session_partagee,
        "envoyees": len(sortie), "echouees": len(ko),
        "erreurs": dict(Counter(r.get("error", str(r.get("status"))) for r in ko)),
        "ms_median": lat[len(lat) // 2], "ms_max": lat[-1],
        "duree_s": round(duree, 1),
    }


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    url = f"https://{ip}/di/v1/products/1/wusrd"

    plan = [(1, True), (1, False), (3, True), (3, False)]
    res = []
    for conc, part in plan:
        r = campagne(url, conc, part)
        res.append(r)
        etiquette = (f"{conc} en vol, "
                     f"{'Session partagee' if part else 'une Session par fil'}")
        print(f"{etiquette:<40} {r['envoyees'] - r['echouees']:>2}/{r['envoyees']} ok"
              f" | med {r['ms_median']:>6.0f} max {r['ms_max']:>7.0f} ms | {r['duree_s']:>5.1f} s")
        if r["erreurs"]:
            print(f"{'':>40} {r['erreurs']}")
        time.sleep(20)

    nom = f"repro-requests-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
