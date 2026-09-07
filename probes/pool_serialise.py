"""Le correctif de l'issue #8, mesure : borner le pool de connexions a une seule.

L'appareil ne sert qu'une connexion TLS a la fois — c'est etabli (docs/somneo-api.md §7).
Or `pysomneo` monte son adaptateur ainsi (`api.py`, branche master) :

    adapter = HTTPAdapter(
        pool_connections=5,
        pool_maxsize=5,
        max_retries=Retry(total=3, backoff_factor=2.0, ...),
        pool_block=False,          # <- pool plein : on ouvre EN PLUS, on n'attend pas
    )

Le correctif de mai 2026 (« Improve timeout resilience and connection pooling ») a ajoute des
reessais, un backoff et une reinitialisation du pool : il traite le symptome. **La cause reste
autorisee par la configuration** — jusqu'a cinq connexions simultanees vers un appareil qui
n'en sert qu'une, et `pool_block=False` qui laisse en ouvrir davantage encore.

Hypothese a verifier : `pool_connections=1, pool_maxsize=1, pool_block=True` fait sérialiser
urllib3 lui-meme. La deuxieme requete attend la liberation de la connexion au lieu d'en ouvrir
une seconde — la condition d'echec disparait sans verrou applicatif ni refonte.

Trois conditions, meme charge, meme appareil :
  A. pool par defaut (5/5, block=False)  — l'existant
  B. pool borne    (1/1, block=True)     — le correctif propose
  C. pool borne, sans les reessais       — pour distinguer ce qui vient du pool de ce qui
                                           vient des reessais qui masquent les echecs

Lecture seule : que des GET. Rien n'est ecrit sur l'appareil.
"""
import json
import sys
import threading
import time

sys.path.insert(0, ".")

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from somneo_probe import discover

urllib3.disable_warnings()

PORTS = ["wusrd", "wusts", "wungt", "wulgt", "wudsk", "wuply", "wutmr"]
FILS = 3
TOURS = 7


def session(pool, block, retries):
    s = requests.Session()
    s.verify = False
    adapter = HTTPAdapter(
        pool_connections=pool,
        pool_maxsize=pool,
        pool_block=block,
        max_retries=(
            Retry(total=retries, backoff_factor=2.0,
                  status_forcelist=[500, 502, 503, 504],
                  allowed_methods=["GET", "PUT", "POST"])
            if retries else 0
        ),
    )
    s.mount("https://", adapter)
    return s


def condition(ip, libelle, pool, block, retries):
    """`FILS` fils lisent `TOURS` ports chacun, en meme temps, sur une session partagee.

    C'est la forme exacte de `fetch_data()` appele par plusieurs consommateurs — le cas
    decrit dans l'issue #8.
    """
    print("=" * 68)
    print(f"{libelle}")
    print(f"   pool={pool} block={block} retries={retries}")
    print("=" * 68)

    s = session(pool, block, retries)
    resultats = []
    verrou = threading.Lock()
    depart = threading.Barrier(FILS)

    def travail(n):
        depart.wait()
        for port in PORTS[:TOURS]:
            t0 = time.monotonic()
            try:
                r = s.get(f"https://{ip}/di/v1/products/1/{port}", timeout=20)
                issue = {"ok": r.status_code == 200, "status": r.status_code}
            except Exception as exc:
                issue = {"ok": False, "erreur": type(exc).__name__,
                         "detail": str(exc)[:90]}
            issue["ms"] = round((time.monotonic() - t0) * 1000, 1)
            issue["fil"] = n
            with verrou:
                resultats.append(issue)

    fils = [threading.Thread(target=travail, args=(n,)) for n in range(FILS)]
    t0 = time.monotonic()
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    duree = time.monotonic() - t0
    s.close()

    ok = sum(1 for r in resultats if r["ok"])
    total = len(resultats)
    lat = sorted(r["ms"] for r in resultats)
    erreurs = {}
    for r in resultats:
        if not r["ok"]:
            erreurs[r.get("erreur") or r.get("status")] = \
                erreurs.get(r.get("erreur") or r.get("status"), 0) + 1

    print(f"   reussite : {ok}/{total}")
    print(f"   duree    : {duree:.1f} s   |  med {lat[len(lat) // 2]:.0f} ms  "
          f"max {lat[-1]:.0f} ms")
    print(f"   erreurs  : {erreurs or 'aucune'}")
    print()
    return {"libelle": libelle, "pool": pool, "block": block, "retries": retries,
            "ok": ok, "total": total, "duree_s": round(duree, 1),
            "ms_median": lat[len(lat) // 2], "ms_max": lat[-1], "erreurs": erreurs}


def decouvrir(essais=4):
    """SSDP rate parfois du premier coup — un M-SEARCH perdu suffit. Reessayer avant d'abandonner."""
    for n in range(1, essais + 1):
        ip = discover(timeout=6)
        if ip:
            return ip
        print(f"  SSDP sans reponse (essai {n}/{essais})")
        time.sleep(3)
    return None


def main():
    ip = decouvrir()
    if not ip:
        print("Somneo introuvable en SSDP apres plusieurs essais — sonde abandonnee.")
        return 1
    print(f"Somneo decouvert. {FILS} fils x {TOURS} lectures par condition.\n")

    releve = []
    conditions = [
        ("A. pool par defaut de pysomneo (l'existant)", 5, False, 3),
        ("B. pool borne a 1, bloquant (le correctif propose)", 1, True, 3),
        ("C. pool borne a 1, bloquant, sans reessai", 1, True, 0),
    ]
    # Trois series par condition : une seule mesure ne se defend pas devant un mainteneur,
    # et l'ordre est alterne pour qu'un eventuel echauffement de l'appareil ne se confonde
    # pas avec l'effet mesure.
    for serie in range(1, 4):
        print(f"\n########## serie {serie}/3 ##########\n")
        ordre = conditions if serie % 2 else list(reversed(conditions))
        for libelle, pool, block, retries in ordre:
            r = condition(ip, libelle, pool, block, retries)
            r["serie"] = serie
            releve.append(r)
            time.sleep(5)   # laisser l'appareil se remettre entre deux conditions

    print("=" * 68)
    print("Bilan — trois series par condition")
    print("=" * 68)
    for libelle, pool, block, retries in conditions:
        s = [r for r in releve if r["libelle"] == libelle]
        durees = sorted(r["duree_s"] for r in s)
        ok = sum(r["ok"] for r in s)
        total = sum(r["total"] for r in s)
        maxs = max(r["ms_max"] for r in s)
        print(f"   {ok:>2}/{total}   duree {durees}  mediane des durees {durees[1]:>5.1f} s"
              f"   pire requete {maxs:>7.0f} ms   {libelle}")

    nom = time.strftime("pool-serialise-%Y%m%dT%H%M%S.json")
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(releve, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
