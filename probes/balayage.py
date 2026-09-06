"""Balayage exhaustif de l'espace des noms de ports `wu` + trois lettres.

Le relevé du 31 août 2026 concluait que cet espace « ne peut pas être balayé sur un appareil
qui tombe en timeout ». La mesure du 6 septembre le contredit : un port inconnu est refusé en
~16 ms, et l'appareil encaisse un flux sérialisé sans faiblir. 26^3 = 17 576 noms, environ
36 minutes.

C'est le seul moyen de transformer « liste minorante vérifiée » en « liste exhaustive » pour la
convention `wuXXX`, et de répondre aux issues #16 et #6 autrement que par une lecture d'APK.

Sérialisé, une requête en vol, reprise possible : le journal est écrit au fil de l'eau et le
balayage repart de la où il s'est arrete si on le relance. Lecture seule.
"""
import itertools
import json
import os
import string
import sys
import time

sys.path.insert(0, ".")
from somneo_probe import discover, get

PREFIXE = os.environ.get("BALAYAGE_PREFIXE", "wu")
LONGUEUR = int(os.environ.get("BALAYAGE_LETTRES", "3"))
JOURNAL = f"balayage-{PREFIXE or 'nu'}{LONGUEUR}.jsonl"
# Borne horaire : le balayage long doit rendre l'appareil avant le retour de Victor,
# pour que le superviseur relance la capture. 0 = pas de limite.
FIN = float(os.environ.get("BALAYAGE_FIN", "0"))
PAUSE = 0.02          # l'appareil encaisse ; on reste poli sans etre lent
RAPPORT = 500


def deja_faits():
    """Reprise : on ne rejoue pas ce qui a deja ete essaye."""
    vus = set()
    if os.path.exists(JOURNAL):
        for ligne in open(JOURNAL, encoding="utf-8"):
            try:
                vus.add(json.loads(ligne)["port"])
            except Exception:
                pass
    return vus


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1

    vus = deja_faits()
    noms = [PREFIXE + "".join(t)
            for t in itertools.product(string.ascii_lowercase, repeat=LONGUEUR)]
    restants = [n for n in noms if n not in vus]
    print(f"{len(noms)} noms au total, {len(vus)} deja essayes, {len(restants)} restants",
          flush=True)

    trouves = []
    codes = {}
    debut = time.time()
    with open(JOURNAL, "a", encoding="utf-8") as fh:
        for i, nom in enumerate(restants, 1):
            if FIN and time.time() > FIN:
                print(f"  borne horaire atteinte, arret propre a {i}/{len(restants)}",
                      flush=True)
                break
            r = get(ip, 1, nom, timeout=10)
            code = str(r.get("status") or r.get("error", "?"))[:40]
            codes[code] = codes.get(code, 0) + 1
            ligne = {"port": nom, "status": r.get("status"), "ok": r["ok"],
                     "ms": r["ms"], "error": r.get("error")}
            if r["ok"]:
                ligne["body"] = r.get("body")
                trouves.append(nom)
                print(f"  !! {nom} REPOND : {json.dumps(r.get('body'))[:150]}", flush=True)
            fh.write(json.dumps(ligne, ensure_ascii=False) + "\n")
            fh.flush()
            if i % RAPPORT == 0:
                ecoule = time.time() - debut
                reste = (len(restants) - i) * ecoule / i
                print(f"  {i}/{len(restants)}  ({ecoule/60:.1f} min ecoulees, "
                      f"~{reste/60:.1f} min restantes)  codes={codes}", flush=True)
            time.sleep(PAUSE)

    print(f"\nTermine en {(time.time()-debut)/60:.1f} min. Codes : {codes}")
    print(f"Ports repondant : {trouves if trouves else 'aucun nouveau'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
