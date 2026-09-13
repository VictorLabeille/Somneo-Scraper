"""Derive de l'horloge du reveil, reconstituee depuis les journaux de `capture.py`.

Lecture seule, et meme pas de l'appareil : la campagne de capture lit `products/0/time` une
fois par heure depuis le 2026-09-06, et chaque enregistrement porte l'heure de la carte
(`ts`, synchronisee NTP) et la duree de la requete (`ms`). De quoi tracer le decalage du
reveil sur des jours sans lui envoyer une seule requete de plus.

  decalage = heure du reveil - heure de la carte au milieu de la requete

Le reveil ne donne l'heure qu'a la seconde, et il tronque : le decalage vrai vaut le decalage
rapporte plus un residu inconnu dans [0, 1). Ce residu est le meme a chaque point, donc il ne
touche ni la PENTE ni les SAUTS — les deux choses qu'on cherche. Il interdit seulement de lire
un decalage absolu ici : pour cela, `ecriture_heure.py --lecture` mesure au centieme, en
guettant l'instant ou la seconde du reveil change.

Un saut vers le bas de plus d'une seconde entre deux points signale une remise a l'heure que
le reveil s'est faite tout seul. `--seuil` regle ce qui compte comme un saut.

Usage : python3 derive_horloge.py capture/*.jsonl [--seuil 1.0]
Sortie : JSON sur la sortie standard — points (heures ecoulees, decalage), sauts, et la pente
des segments entre deux sauts. Aucun corps de port n'est lu hors de `products/0/time` : rien
des capteurs ni des alarmes n'entre ici.
"""
import argparse
import json
import sys
from datetime import datetime


def points(chemins):
    """(instant carte, decalage) pour chaque lecture reussie de `products/0/time`."""
    out = []
    for chemin in chemins:
        with open(chemin, encoding="utf-8") as fh:
            for ligne in fh:
                if '"time"' not in ligne:          # filtre grossier : 99 % des lignes sautees
                    continue
                try:
                    rec = json.loads(ligne)
                except ValueError:
                    continue
                if rec.get("port") != "time" or rec.get("product") != 0 or not rec.get("ok"):
                    continue
                brut = (rec.get("body") or {}).get("datetime")
                try:
                    appareil = datetime.fromisoformat(brut).timestamp()
                except (TypeError, ValueError):
                    continue
                milieu = rec["ts"] + (rec.get("ms") or 0.0) / 2000.0
                out.append((milieu, round(appareil - milieu, 3)))
    out.sort()
    return out


def pente(segment):
    """Moindres carres sur un segment : secondes de decalage gagnees par jour."""
    n = len(segment)
    if n < 3:
        return None
    sx = sum(t for t, _ in segment)
    sy = sum(v for _, v in segment)
    sxx = sum(t * t for t, _ in segment)
    sxy = sum(t * v for t, v in segment)
    denom = n * sxx - sx * sx
    return None if denom == 0 else round((n * sxy - sx * sy) / denom * 86400, 2)


def main():
    p = argparse.ArgumentParser(description="derive de l'horloge du reveil")
    p.add_argument("journaux", nargs="+")
    p.add_argument("--seuil", type=float, default=1.0,
                   help="saut vers le bas, en s, qui compte comme une remise a l'heure")
    a = p.parse_args()

    pts = points(a.journaux)
    if len(pts) < 2:
        sys.exit("pas assez de lectures de products/0/time")
    t0 = pts[0][0]

    sauts, segments, courant = [], [], [pts[0]]
    for prec, cur in zip(pts, pts[1:]):
        if cur[1] - prec[1] <= -a.seuil:
            sauts.append({"h": round((cur[0] - t0) / 3600, 2),
                          "avant_s": prec[1], "apres_s": cur[1],
                          "amplitude_s": round(cur[1] - prec[1], 3),
                          "intervalle_min": round((cur[0] - prec[0]) / 60, 1)})
            segments.append(courant)
            courant = []
        courant.append(cur)
    segments.append(courant)

    resume = [{"debut_h": round((s[0][0] - t0) / 3600, 2),
               "fin_h": round((s[-1][0] - t0) / 3600, 2), "points": len(s),
               "decalage_debut_s": s[0][1], "decalage_fin_s": s[-1][1],
               "pente_s_par_jour": pente(s)} for s in segments if s]

    json.dump({"source": "probes/capture.py, port products/0/time, ~1 lecture par heure",
               "convention": "decalage = heure du reveil - heure de la carte (NTP), au milieu "
                             "de la requete ; le reveil tronque a la seconde, donc chaque "
                             "valeur est basse d'un residu constant dans [0, 1)",
               "lectures": len(pts), "duree_h": round((pts[-1][0] - t0) / 3600, 2),
               "seuil_saut_s": a.seuil, "sauts": sauts, "segments": resume,
               "points": [[round((t - t0) / 3600, 3), v] for t, v in pts]},
              sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
