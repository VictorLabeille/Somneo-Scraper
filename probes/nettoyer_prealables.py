"""Nettoie les releves des sondes d'ecriture P1, P2, P3 avant de les verser dans results/.

Les releves bruts restent sur la carte, ou dans un repertoire temporaire : ils portent l'heure
du dernier coucher reel (`wungt`), les heures d'alarme (`aalms`, `prfwu`) et des corps de port
entiers. Ce script n'en garde que ce qui porte la demonstration — statuts, durees, verdicts,
chemins des champs modifies, decalages d'horloge — et il ne recopie AUCUN corps de port.

Les heures de `wungt` deviennent des ecarts a l'heure du reveil lue juste avant l'ecriture, et
seulement quand la sonde les a posees elle-meme : un `tendb` anterieur a la sonde, soustrait a
la date du releve, redonnerait l'heure d'une alarme reelle. Au-dela d'une heure d'ecart, la
valeur est remplacee par « anterieure a la sonde ».

Un controle final refuse la sortie si une heure absolue ou un champ d'alarme s'y retrouve.
Seule exception : la date legale de la bascule d'heure (`dstchangeover`), publique.

Usage : python3 nettoyer_prealables.py RELEVE.jsonl > results/NOM.json
Bibliotheque standard uniquement ; tourne sur le poste de dev, pas sur la carte.
"""
import json
import re
import sys
from datetime import datetime, timezone

LIMITE_ECART_S = 3600
CLES_INTERDITES = {"almhr", "almmn", "aalms", "ayear", "amnth", "alday", "pszhr", "pszmn",
                   "tg2bd", "tendb", "datetime", "serial", "macaddress", "ssid"}
HEURE_ABSOLUE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}")


def heure(v):
    try:
        return datetime.fromisoformat(v)
    except (TypeError, ValueError):
        return None


def ecart(v, ref):
    """Secondes entre deux heures ISO ; None si l'une manque ; masque au-dela d'une heure."""
    a, b = heure(v), heure(ref)
    if a is None or b is None:
        return None
    s = round((a - b).total_seconds(), 3)
    return s if abs(s) <= LIMITE_ECART_S else "anterieure a la sonde"


def chemins(liste):
    return [c[0] for c in (liste or [])]


def requete(r):
    if not r:
        return None
    return {k: r[k] for k in ("methode", "port", "status", "ms", "ok", "error") if k in r}


def corps_public(r):
    """Corps de reponse d'un PUT : seulement s'il s'agit d'une erreur de l'appareil."""
    b = (r or {}).get("body")
    return b if isinstance(b, dict) and set(b) == {"error"} else None


# ---- P3 -----------------------------------------------------------------------------------

def p3(recs):
    out = {"sonde": "probes/selection_profil.py", "essais": [], "passes": []}
    for r in recs:
        t = r["type"]
        if t == "debut":
            out["mode"] = r["mode"]
        elif t == "P3-1":
            out["P3-1"] = {"stable_sans_ecriture": r["stable"], "wusts": r["wusts"]}
        elif t == "choix":
            out["choix"] = {k: r[k] for k in ("initial", "dormant", "profil2")}
        elif t == "lecture":
            slash = r["wualm_slash"]
            b = slash.get("body") or {}
            out["wualm_slash"] = {"status": slash.get("status"), "cles": sorted(b),
                                  "sous_objets_vides": sorted(k for k, v in b.items() if v == {})}
        elif t == "essai":
            if "ecarte" in r:
                out["essais"].append({k: r[k] for k in ("test", "rep", "n", "ecarte")})
                continue
            out["essais"].append({
                "test": r["test"], "rep": r["rep"], "n": r["n"], "put": requete(r["put"]),
                "jugement": r["jugement"], "champs_changes": chemins(r["changes"]),
                "inattendus": chemins(r["inattendus"]), "effets": chemins(r["effets"]),
                "retour": requete(r["retour"]), "retour_conforme": r["retour_conforme"],
                "conforme": r["conforme"]})
        elif t == "passe":
            out["passes"].append({"k": r["k"], "anomalies": [a["n"] for a in r["anomalies"]],
                                  "retour_conforme": r["retour_conforme"]})
        elif t == "P3-5":
            out["P3-5"] = {k: r[k] for k in ("passes_egales", "differents",
                                             "profil_initial_coherent")}
        elif t == "hors_bornes":
            out.setdefault("P3-6", []).append({
                "prfnr_ecrit": r["n"], "put": requete(r["put"]),
                "put_corps": corps_public(r["put"]),
                "prfnr_relu": ((r.get("etat") or {}).get("wualm") or {}).get("prfnr"),
                "champs_changes": chemins(r["ecart"]), "retour_conforme": r["retour_conforme"]})
        elif t == "restauration":
            out["restauration"] = {"conforme": r["conforme"], "ecart": chemins(r["ecart"])}
        elif t in ("arret_securite", "interruption"):
            out.setdefault("incidents", []).append({k: r[k] for k in ("type", "raison") if k in r})
        elif t == "fin":
            out["fin"] = {k: r[k] for k in ("bilan", "restaure", "abandon") if k in r}
    return out


# ---- P1 -----------------------------------------------------------------------------------

def ecriture(e):
    """Une ecriture de P1 : statut, et les heures de `wungt` en ecarts a l'heure lue avant."""
    if not e:
        return None
    ref, fin = e["heure_avant"], e["final"] or {}
    return {"etiquette": e["etiquette"].split(" {")[0], "champs_ecrits": sorted(e["charge"]),
            "put": requete(e["put"]), "put_corps": corps_public(e["put"]),
            "duree_fenetre_s": ecart(e["heure_apres"], ref), "night": fin.get("night"),
            "tg2bd_s": ecart(fin.get("tg2bd"), ref), "tendb_s": ecart(fin.get("tendb"), ref),
            "relus_identiques_05_5_30s": e["stable"]}


def p1(recs):
    out = {"sonde": "probes/ecriture_wungt.py", "cycles": [], "inscriptions": [],
           "convention": "tg2bd_s / tendb_s : secondes depuis l'heure du reveil lue juste "
                         "avant l'ecriture ; « anterieure a la sonde » au-dela d'une heure"}
    for r in recs:
        t = r["type"]
        if t == "debut":
            out["mode"] = r["mode"]
        elif t == "initial":
            out["initial"] = {"wusts": r["wusts"], "night": (r["wungt"] or {}).get("night")}
        elif t == "cycle":
            j = {}
            for cle, v in r["jugement"].items():
                j[cle] = ({k: (chemins(x) if k == "effets" else x) for k, x in v.items()}
                          if isinstance(v, dict) else v)
            out["cycles"].append({"k": r["k"], "jugement": j,
                                  "ecritures": [ecriture(r[c]) for c in ("o1", "o2", "c1", "c2")]})
        elif t == "inscription":
            if "ecarte" in r:
                out["inscriptions"].append({k: r[k] for k in ("test", "ecarte")})
                continue
            e = r["ecriture"]
            out["inscriptions"].append({
                "test": r["test"], "champ": r["champ"], "recul_s": r["recul_s"],
                "champs_ecrits": sorted(r["charge"]), "issue": r["issue"],
                "cible_s": ecart(r["cible"], e["heure_avant"]), "ecriture": ecriture(e),
                "ouverture_prealable": ecriture(r.get("ouverture")),
                "night_apres": r["night_apres"], "champs_changes": r["champs"],
                "effets": chemins(r["effets"]), "close_a_la_fin": r["close_a_la_fin"]})
        elif t == "tenue":
            out["P1-5"] = {"lectures": len(r["lectures"]), "derives": len(r["derives"]),
                           "echecs": r["echecs"], "ouverture": ecriture(r["ouverture"]),
                           "fermeture": ecriture(r["fermeture"])}
        elif t == "remise":
            out.setdefault("remises", []).append(ecriture(r["ecriture"]))
        elif t == "restauration":
            out["restauration"] = {"session_close": r["session_close"],
                                   "heures_reposees": [requete(x) for x in r["remises"]],
                                   "identique_au_debut": r["identique_au_debut"]}
        elif t == "resume":
            out["issues"] = r["issues"]
            out["champs_ouverture"] = r["champs_ouverture"]
        elif t == "controle":
            j = dict(r["jugement"])
            j["effets_hors_wungt"] = chemins(j["effets_hors_wungt"])
            out["P1-9"] = {"jugement": j}
        elif t == "controle_fermeture":
            out.setdefault("P1-9", {})["fermeture"] = {"jugement": r["jugement"],
                                                       "ecriture": ecriture(r["ecriture"])}
        elif t in ("arret_securite", "interruption"):
            out.setdefault("incidents", []).append({k: r[k] for k in ("type", "raison") if k in r})
        elif t == "fin":
            out["fin"] = {k: r[k] for k in ("abandon",) if k in r}
    return out


# ---- P2 -----------------------------------------------------------------------------------

def pose(e, origine):
    if not e:
        return None
    return {"cible_moins_origine_s": None if origine is None else round(e["cible"] - origine, 3),
            "forme": e["forme"], "put": requete(e["put"]), "put_corps": corps_public(e["put"]),
            "aller_estime_s": e["aller_estime"], "retard_envoi_s": e["retard_envoi"]}


def remise(r, origine):
    if not r:
        return None
    return {"conforme": r["conforme"], "residuel_s": r["residuel"],
            "essais": [pose(x["ecriture"], origine) for x in r["etapes"]]}


def p2(recs):
    out = {"sonde": "probes/ecriture_heure.py",
           "convention": "decalages en secondes : horloge du reveil - horloge de la carte (NTP)"}
    origine = None
    for r in recs:
        t = r["type"]
        if t == "debut":
            out["mode"] = r["mode"]
        elif t == "P2-1":
            origine = r["origine"]
            out["P2-1"] = {
                "mesures": [{"decalage_s": m["decalage"],
                             "incertitudes_s": [x["incertitude"] for x in m["estimations"]]}
                            for m in r["mesures"]],
                "origine_s": origine, "dispersion_s": r["dispersion"],
                "origine_wutim_s": r["origine_wutim"],
                "time_initial": {k: v for k, v in (r["time0"] or {}).items() if k != "datetime"},
                "wutms_initial": r["wutms0"]}
        elif t == "P2-2":
            out["P2-2"] = {"resultats": [
                {"port": x["port"], "champ": x["champ"], "absent": x.get("absent", False),
                 "put": requete(x.get("put")), "put_corps": corps_public(x.get("put")),
                 "relu_inchange": x.get("inchange"), "autres_inchanges": x.get("autres_inchanges")}
                for x in r["resultats"]], "decalage_apres_s": r["decalage_apres"]}
        elif t == "P2-6":
            out["P2-6"] = ({"saute": r["saute"]} if "saute" in r else
                           {"nouvelle_valeur": r["nouveau"], "put": requete(r["put"]),
                            "put_restauration": requete(r["put_restauration"]),
                            "jugement": r["jugement"]})
        elif t == "P2-8":
            out["P2-8"] = {"pose": pose(r["ecriture"], origine), "ecart_grossier_s": r["grossier"],
                           "deplacement_mesure_s": None if r["decalage"] is None or origine is None
                           else round(r["decalage"] - origine, 3),
                           "verdict": r["verdict"], "remise": remise(r["remise"], origine)}
        elif t == "P2-5":
            out.setdefault("P2-5", []).append({"forme": r["forme"], "pose": pose(r["ecriture"], origine),
                                               "jugement": r["jugement"],
                                               "remise": remise(r["remise"], origine)})
        elif t == "P2-3":
            out.setdefault("P2-3", []).append({"rep": r["rep"], "pose": pose(r["ecriture"], origine),
                                               "jugement": r["jugement"],
                                               "remise_P2-4": remise(r["remise"], origine)})
        elif t == "P2-7":
            out["P2-7"] = {"suivi": [{"t_s": x["t"], "ecart_au_deplacement_s": x["ecart_au_deplacement"]}
                                     for x in r["suivi"]], "bouge_seul": r["bouge_seul"]}
        elif t == "P2-9":
            out.setdefault("P2-9", []).append({"etape": r.get("etape", "fin"),
                                               "effets": chemins(r["effets"])})
        elif t == "restauration":
            out["restauration"] = {"horloge_conforme": r["horloge_conforme"],
                                   "champs_conformes": r["champs_conformes"],
                                   "ecart_final_s": None if r["decalage_final"] is None
                                   else round(r["decalage_final"] - r["origine"], 3),
                                   "ecart_wutim_final_s": r.get("ecart_wutim_final")}
        elif t in ("arret_securite", "interruption"):
            out.setdefault("incidents", []).append({k: r[k] for k in ("type", "raison") if k in r})
        elif t == "fin":
            out["fin"] = {k: r[k] for k in ("bilan", "restaure", "abandon") if k in r}
    return out


# ---- controle et sortie -------------------------------------------------------------------

def fuites(obj, exceptions, chemin=""):
    """Cles interdites, et heures absolues hors exceptions, n'importe ou dans la sortie."""
    trouve = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in CLES_INTERDITES:
                trouve.append(f"{chemin}.{k}")
            trouve += fuites(v, exceptions, f"{chemin}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            trouve += fuites(v, exceptions, f"{chemin}[{i}]")
    elif isinstance(obj, str) and HEURE_ABSOLUE.search(obj) and obj not in exceptions:
        trouve.append(f"{chemin} = {obj}")
    return trouve


def main():
    chemin = sys.argv[1]
    recs = [json.loads(l) for l in open(chemin, encoding="utf-8") if l.strip()]
    nom = chemin.rsplit("/", 1)[-1]
    if nom.startswith("selection-profil"):
        out = p3(recs)
    elif nom.startswith("ecriture-wungt"):
        out = p1(recs)
    elif nom.startswith("ecriture-heure"):
        out = p2(recs)
    else:
        sys.exit(f"releve inconnu : {nom}")
    out["date"] = datetime.fromtimestamp(recs[0]["ts"], timezone.utc).strftime("%Y-%m-%d")
    out["releve_brut"] = f"{nom} (non versionne)"
    exceptions = set()
    for r in recs:                        # la bascule d'heure est une date legale, publique
        if r["type"] == "P2-1":
            exceptions.add((r["time0"] or {}).get("dstchangeover"))
        if r["type"] == "P2-6" and "nouveau" in r:
            exceptions.add(r["nouveau"])
    probleme = fuites(out, exceptions)
    if probleme:
        sys.exit("REFUS, donnees privees dans la sortie :\n  " + "\n  ".join(probleme))
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
