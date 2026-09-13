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

Le script apparie ensuite chaque saut au port `backend`, lu dans le meme lot : si
`lastsignon` a change entre les deux lectures, la remise suit une reconnexion au cloud. Dans
un lot, `time` est lu quelques secondes AVANT `backend` — d'ou la fenetre d'appariement de
120 s, et non la derniere valeur connue, qui daterait de l'heure precedente.

Usage : python3 derive_horloge.py capture/*.jsonl [--seuil 1.0]
Sortie : JSON sur la sortie standard — points (heures ecoulees, decalage), sauts, pente des
segments entre deux sauts, et le recoupement avec les reconnexions. Aucun corps de port n'est
lu hors de `products/0/time`, `0/backend` et `1/wutms` : rien des capteurs ni des alarmes
n'entre ici.
"""
import argparse
import json
import sys
from datetime import datetime


FENETRE_LOT = 120       # s : ecart max entre deux lectures d'un meme lot d'instantane


def lire(chemins):
    """Decalages de l'horloge, reconnexions au cloud, reglages de temps — un seul passage."""
    pts, signons, reglages = [], [], []
    for chemin in chemins:
        with open(chemin, encoding="utf-8") as fh:
            for ligne in fh:
                # filtre grossier : 99 % des lignes sautees sans passer par le decodeur JSON
                if '"time"' not in ligne and '"backend"' not in ligne and '"wutms"' not in ligne:
                    continue
                try:
                    rec = json.loads(ligne)
                except ValueError:
                    continue
                if not rec.get("ok"):
                    continue
                corps = rec.get("body") or {}
                port = rec.get("port")
                if port == "time" and rec.get("product") == 0:
                    try:
                        appareil = datetime.fromisoformat(corps.get("datetime")).timestamp()
                    except (TypeError, ValueError):
                        continue
                    milieu = rec["ts"] + (rec.get("ms") or 0.0) / 2000.0
                    pts.append((milieu, round(appareil - milieu, 3)))
                elif port == "backend":
                    signons.append((rec["ts"], corps.get("lastsignon")))
                elif port == "wutms":
                    reglages.append((rec["ts"], tuple(corps.get(c) for c in
                                                      ("tmsrc", "tmser", "tmsyn", "tmupd"))))
    pts.sort(); signons.sort(); reglages.sort()
    return pts, signons, reglages


def signon_du_lot(ts, signons):
    """`lastsignon` releve dans le meme lot d'instantane que cette lecture d'heure."""
    proches = [(abs(t - ts), ls) for t, ls in signons if abs(t - ts) <= FENETRE_LOT]
    return min(proches)[1] if proches else None


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

    pts, signons, reglages = lire(a.journaux)
    if len(pts) < 2:
        sys.exit("pas assez de lectures de products/0/time")
    t0 = pts[0][0]

    sauts, segments, courant, sans_saut = [], [], [pts[0]], []
    for prec, cur in zip(pts, pts[1:]):
        avant, apres = signon_du_lot(prec[0], signons), signon_du_lot(cur[0], signons)
        neuf = avant is not None and apres is not None and avant != apres
        if cur[1] - prec[1] <= -a.seuil:
            sauts.append({"h": round((cur[0] - t0) / 3600, 2),
                          "avant_s": prec[1], "apres_s": cur[1],
                          "amplitude_s": round(cur[1] - prec[1], 3),
                          "intervalle_min": round((cur[0] - prec[0]) / 60, 1),
                          "sign_on_entre_les_deux": neuf, "lastsignon": apres})
            segments.append(courant)
            courant = []
        elif neuf:
            sans_saut.append({"h": round((cur[0] - t0) / 3600, 2),
                              "ecart_s": round(cur[1] - prec[1], 3),
                              "decalage_avant_s": prec[1], "lastsignon": apres})
        courant.append(cur)
    segments.append(courant)

    resume = [{"debut_h": round((s[0][0] - t0) / 3600, 2),
               "fin_h": round((s[-1][0] - t0) / 3600, 2), "points": len(s),
               "decalage_debut_s": s[0][1], "decalage_fin_s": s[-1][1],
               "pente_s_par_jour": pente(s)} for s in segments if s]

    json.dump({"source": "probes/capture.py, ports products/0/time, 0/backend et 1/wutms",
               "convention": "decalage = heure du reveil - heure de la carte (NTP), au milieu "
                             "de la requete ; le reveil tronque a la seconde, donc chaque "
                             "valeur est basse d'un residu constant dans [0, 1)",
               "lectures": len(pts), "duree_h": round((pts[-1][0] - t0) / 3600, 2),
               "seuil_saut_s": a.seuil, "sauts": sauts, "segments": resume,
               "sauts_suivant_un_sign_on": sum(1 for s in sauts if s["sign_on_entre_les_deux"]),
               "sign_ons_sans_saut": sans_saut,
               "reglages_de_temps_distincts":
                   [list(v) for v in dict.fromkeys(v for _, v in reglages)],
               "points": [[round((t - t0) / 3600, 3), v] for t, v in pts]},
              sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
