"""Issue #8 de pysomneo — l'experience decisive. Lecture seule.

Le rapporteur accuse `requests.Session` : ses appels nus passent, ceux de la Session expirent.
Ce qui distingue les deux, c'est la **connexion reutilisee** (keep-alive) et la possibilite
d'avoir plusieurs requetes en vol. On croise donc les deux axes a nombre de requetes egal :

    serialise / concurrent   x   connexion neuve / connexion reutilisee

Une seule de ces quatre cases doit echouer si l'hypothese du rapporteur est juste (celle qui
reutilise la connexion). Si aucune n'echoue, l'explication est encore ailleurs, et il faut le
dire plutot que de publier une conjecture.

Aucune ecriture. Que des GET.
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
PAR_CONDITION = 21          # divisible par 1, 3 et 7 : meme charge dans tous les cas
REPOS = 20.0                # s entre deux conditions, pour ne pas les faire deteindre


def _ouvrir(ip):
    return http.client.HTTPSConnection(ip, timeout=20, context=CTX)


def _worker(ip, n, reutiliser, sortie, verrou):
    """Envoie n requetes. Garde sa connexion ouverte si `reutiliser`, sinon en rouvre une."""
    conn = _ouvrir(ip) if reutiliser else None
    for _ in range(n):
        propre = conn is None
        if propre:
            conn = _ouvrir(ip)
        debut = time.monotonic()
        rec = {"ok": False}
        try:
            conn.request("GET", CHEMIN,
                         headers={"Connection": "keep-alive" if reutiliser else "close"})
            resp = conn.getresponse()
            resp.read()
            rec = {"ok": resp.status == 200, "status": resp.status}
        except Exception as exc:
            rec = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
            try:
                conn.close()
            except Exception:
                pass
            conn = _ouvrir(ip) if reutiliser else None
        rec["ms"] = round((time.monotonic() - debut) * 1000, 1)
        with verrou:
            sortie.append(rec)
        if not reutiliser and conn is not None:
            conn.close()
            conn = None
    if conn is not None:
        conn.close()


def condition(ip, libelle, concurrence, reutiliser):
    avant, _, _ = heap(ip)
    time.sleep(1.0)
    sortie, verrou = [], threading.Lock()
    part = PAR_CONDITION // concurrence
    debut = time.monotonic()
    fils = [threading.Thread(target=_worker, args=(ip, part, reutiliser, sortie, verrou))
            for _ in range(concurrence)]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    duree = time.monotonic() - debut
    time.sleep(1.0)
    apres, taille, _ = heap(ip)

    lat = sorted(r["ms"] for r in sortie)
    ko = [r for r in sortie if not r["ok"]]
    return {
        "libelle": libelle, "concurrence": concurrence, "connexion_reutilisee": reutiliser,
        "envoyees": len(sortie), "echouees": len(ko),
        "erreurs": sorted({r.get("error", r.get("status", "")) for r in ko} - {""}),
        "ms_min": lat[0], "ms_median": lat[len(lat) // 2], "ms_max": lat[-1],
        "duree_totale_s": round(duree, 1),
        "heap_avant": avant, "heap_apres": apres, "heap_size": taille,
    }


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    print("Note : la capture nocturne tourne en fond (~3 requetes/min). Charge negligeable,\n"
          "       mais realiste — c'est celle du collecteur.\n")

    plan = [
        ("serialise, connexion neuve", 1, False),
        ("serialise, connexion reutilisee (le cas Session)", 1, True),
        ("3 en vol, connexions neuves", 3, False),
        ("3 en vol, connexions reutilisees", 3, True),
        ("7 en vol, connexions reutilisees", 7, True),
    ]
    resultats = []
    for libelle, conc, reut in plan:
        r = condition(ip, libelle, conc, reut)
        resultats.append(r)
        print(f"{libelle:<50} {r['envoyees'] - r['echouees']:>2}/{r['envoyees']} ok"
              f" | med {r['ms_median']:>6.0f} max {r['ms_max']:>6.0f} ms"
              f" | {r['duree_totale_s']:>5.1f} s"
              f" | tas {r['heap_avant']} -> {r['heap_apres']}")
        if r["erreurs"]:
            print(f"{'':>50} erreurs : {r['erreurs']}")
        time.sleep(REPOS)

    nom = f"concurrence-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(resultats, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")

    echoue = [r for r in resultats if r["echouees"]]
    if not echoue:
        print("\nAUCUNE condition n'echoue. L'hypothese « c'est la Session » n'est pas\n"
              "reproduite sur ce firmware : ne rien affirmer sur l'issue #8.")
    else:
        print("\nConditions en echec : " + ", ".join(r["libelle"] for r in echoue))
    return 0


if __name__ == "__main__":
    sys.exit(main())
