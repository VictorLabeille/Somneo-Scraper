"""Rejoue les noms qu'un balayage n'a pas su resoudre — sans quoi il n'est pas exhaustif.

Le balayage des noms de trois lettres du 2026-09-07 a laisse **71 trous** : 67 « connection
refused », 3 timeouts et une erreur SSL, sur 17 576 noms. Ces 71-la n'ont pas repondu `422`,
ils n'ont pas repondu du tout — on ne sait donc pas s'ils existent. Presenter ce balayage comme
exhaustif sans les avoir rejoues serait faux, et c'est le genre d'affirmation qu'un mainteneur
verifie.

Ils sont **consecutifs** (`ddu` a `dgm`) : l'appareil a decroche sur un bloc puis s'est remis.
Une panne momentanee, pas une propriete de ces noms.

Lecture seule. Un nom a la fois, espace, connexion reutilisee.
"""
import json
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from somneo_probe import discover

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PAUSE = 0.3
FICHIER_NOMS = "rattrapage.txt"


def decouvrir(essais=4):
    for n in range(1, essais + 1):
        ip = discover(timeout=6)
        if ip:
            return ip
        print(f"  SSDP sans reponse (essai {n}/{essais})", flush=True)
        time.sleep(3)
    return None


def main():
    with open(FICHIER_NOMS, encoding="utf-8") as fh:
        noms = [l.strip() for l in fh if l.strip()]
    print(f"{len(noms)} noms a rejouer : {noms[0]} … {noms[-1]}\n")

    ip = decouvrir()
    if not ip:
        print("Somneo introuvable en SSDP — sonde abandonnee.")
        return 1

    opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=CTX))
    resultats, restants = [], []
    for i, nom in enumerate(noms, 1):
        url = f"https://{ip}/di/v1/products/1/{nom}"
        rec = {"port": nom}
        try:
            req = urllib.request.Request(url, headers={"Connection": "keep-alive"})
            with opener.open(req, timeout=12) as r:
                rec["status"] = r.status
                rec["brut"] = r.read().decode("utf-8", errors="replace")[:400]
            print(f"  !! {nom} REPOND {rec['status']} : {rec['brut'][:120]}", flush=True)
        except urllib.error.HTTPError as exc:
            rec["status"] = exc.code
        except Exception as exc:
            rec["erreur"] = f"{type(exc).__name__}: {exc}"[:70]
            restants.append(nom)
        resultats.append(rec)
        if i % 20 == 0:
            print(f"  {i}/{len(noms)}", flush=True)
        time.sleep(PAUSE)

    codes = {}
    for r in resultats:
        c = r.get("status") or r.get("erreur", "?")[:30]
        codes[c] = codes.get(c, 0) + 1
    repondants = [r["port"] for r in resultats if r.get("status") == 200]

    print()
    print("=" * 60)
    print(f"  codes : {codes}")
    print(f"  ports repondant : {repondants or 'aucun'}")
    if restants:
        print(f"  ENCORE SANS REPONSE ({len(restants)}) : {' '.join(restants)}")
        print("  -> le balayage n'est toujours pas exhaustif, le dire tel quel.")
    else:
        print("  Tous les noms ont desormais une reponse : le balayage est complet.")

    nom_f = time.strftime("rattrapage-%Y%m%dT%H%M%S.json")
    with open(nom_f, "w", encoding="utf-8") as fh:
        json.dump({"noms": noms, "resultats": resultats, "restants": restants},
                  fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom_f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
