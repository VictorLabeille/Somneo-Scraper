"""P1 — Que fait une ecriture dans `wungt` ? Sonde de refutation, 2026-09-12.

Protocole ecrit et commite AVANT la mesure (plan technique du collecteur, §1, P1).

Le collecteur relaiera les gestes « je me couche » et « je me leve » en ecrivant dans `wungt`,
comme SleepMapper. Et le cadrage (§5) veut qu'un appui fait pendant une panne du reveil y soit
pose plus tard « avec l'heure de l'appui » — ce qui suppose de pouvoir ecrire `tg2bd`
soi-meme. Rien de cela n'a ete mesure : ce qu'on sait de `wungt` en ecriture vient de la
decompilation du 31/08 (« `night` est le seul champ ecrit par l'app »), et ce code decompile
n'existe plus.

Hypotheses, et ce qui les ferait tomber :
  H1  `{"night": true}` ouvre une session ; `tg2bd` prend l'heure du reveil au moment de la
      requete ; rien d'autre ne bouge. Tombe si `tg2bd` sort de la fenetre [heure du reveil lue
      juste avant - 1 s, heure lue juste apres + 1 s], si `tendb` bouge, ou si un autre port
      change.
  H2  `{"night": false}` ferme la session ; `tendb` prend l'heure de la requete. Memes
      criteres, `tg2bd` devant rester inchange.
  H3  `tg2bd` (et `tendb`) s'ecrivent directement. Quatre issues, toutes utiles :
      inscriptible (la valeur relue est celle envoyee), ignore (heure de la requete, ou
      valeur inchangee), refuse (4xx), inattendu.
  H4  Notre ecriture est celle de SleepMapper : mode --controle, apres un appui dans l'app.

Tests :
  P1-1  ouverture                                  } cycle x3, chacun depuis une
  P1-2  seconde ouverture, session deja ouverte    } session close et verifiee ;
  P1-3  fermeture                                  } relectures a +0,5 s, +5 s, +30 s,
  P1-4  seconde fermeture, session deja close      } donc 30 s au moins entre deux ecritures
  P1-6  ouverture portant `tg2bd` = heure du reveil - 10 min, puis - 15 min
  P1-7  `tg2bd` seul (- 5 min) : a) session ouverte, b) session close
  P1-8  `tendb` (- 5 min) : a) dans la fermeture, b) seul, session close
  P1-5  session tenue 10 min, relue chaque minute (en dernier : le plus long)
  P1-9  (--controle) apres un appui « je me couche » dans SleepMapper : memes champs changes
        que par P1-1 ? rien d'autre ? puis notre `night: false` ferme la session de l'app.
  P1-10 (a la main) regarder la facade du reveil pendant la sonde.

Chaque ecriture est datee par l'horloge du reveil (port `time`, a la seconde, lu juste avant
et juste apres sur la meme connexion), puis `wungt` est relu a +0,5 s, +5 s et +30 s. Un
instantane large est pris avant et apres chaque essai : tout changement hors de `wungt`, des
horloges et de `backend.lastsignon` est un effet de bord, et il est note. Au premier effet de
bord, la sonde cesse d'ecrire et passe a la restauration : le repeter serait l'aggraver.

Restauration : session close. Si `tg2bd` et `tendb` se sont montres inscriptibles seuls
(P1-7b, P1-8b), leurs valeurs initiales sont reposees et `wungt` doit etre identique a celui du
debut ; sinon les heures de l'essai y restent, ecrasees au prochain appui.

`--lecture` : aucune ecriture. `--ecriture` : P1-1 a P1-8. `--controle --reference F` : P1-9,
F etant le releve de `--ecriture`.

ECRITURE : `wungt` uniquement. Jamais `wualm`, jamais la lumiere, jamais `fac`. Abandon si une
session est deja ouverte (ce serait une vraie nuit) ou si une alarme est en cours.

Arrete `capture.py` au demarrage (SIGTERM par PID) ; le superviseur la relance a la fin.
Releve brut : il contient l'heure du dernier coucher reel (`tg2bd` initial). NE PAS le verser
tel quel dans probes/results/ — heures converties en ecarts a l'ecriture.
"""
import argparse
import json
import sys
import time
from datetime import timedelta

from somneo_session import (ACCELERATION, Arret, Inattendu, Journal, Session, alarme_en_cours,
                            arreter_capture, attendre_depuis, champs_changes, dormir,
                            effets_de_bord, heure_appareil, instantane, installer_signaux, iso,
                            lever_arret, lire_heure, trouver, verifier)

RELECTURES = (0.5, 5.0, 30.0)
CYCLES = 3
TENUE_S = 600
PAS_TENUE_S = 60
RECULS_P16 = (600, 900)
RECUL = 300
TOLERANCE = timedelta(seconds=1)
DUREE_RESTAURATION = 600
ECRIT = ("1/wungt",)


def wungt(s):
    return s.corps(1, "wungt")


def ecrire(s, charge, etiquette):
    """Une ecriture dans `wungt`, datee par l'horloge du reveil, puis relue trois fois."""
    verifier()
    avant, _ = heure_appareil(s)          # lu juste avant : valide aussi la connexion
    put = s.put(1, "wungt", charge)
    t_put = time.monotonic()
    apres, _ = heure_appareil(s)
    relus = []
    for delai in RELECTURES:
        attendre_depuis(t_put, delai)
        relus.append({"delai": delai, "wungt": wungt(s)})
    return {"etiquette": etiquette, "charge": charge, "put": put, "heure_avant": iso(avant),
            "heure_apres": iso(apres), "relus": relus, "final": relus[-1]["wungt"] or {},
            "stable": all(r["wungt"] == relus[0]["wungt"] for r in relus)}


def a_la_requete(valeur, e):
    """La valeur tombe-t-elle dans [avant - 1 s, apres + 1 s] ? None si non datable."""
    v, a, b = lire_heure(valeur), lire_heure(e["heure_avant"]), lire_heure(e["heure_apres"])
    if v is None or a is None or b is None:
        return None
    return a - TOLERANCE <= v <= b + TOLERANCE


def classer(e, champ, cible, valeur_avant):
    """Issue d'une ecriture directe de `champ`."""
    put = e["put"]
    if not put["ok"]:
        return "refuse" if "status" in put else "erreur de transport"
    relu = e["final"].get(champ)
    if relu == cible or (lire_heure(relu) is not None and lire_heure(relu) == lire_heure(cible)):
        return "inscriptible"
    if relu == valeur_avant:
        return "ignore (inchange)"
    if a_la_requete(relu, e):
        return "ignore (heure de la requete)"
    return "inattendu"


def fermee(s, rel):
    """Session close et verifiee ; la ferme si besoin. None si c'est impossible."""
    w = wungt(s)
    if w is not None and w.get("night") is False:
        return w
    rel.ecrire(type="remise", ecriture=ecrire(s, {"night": False}, "remise a l'etat ferme"))
    w = wungt(s)
    return w if w is not None and w.get("night") is False else None


def depart_verifie(s, rel, test):
    depart = fermee(s, rel)
    if depart is None:
        rel.ecrire(type="essai", test=test, ecarte="impossible de partir d'une session close")
        print(f"{test} : ECARTE, session impossible a fermer", flush=True)
    return depart


def cycle(s, rel, k):
    depart = depart_verifie(s, rel, f"cycle {k}")
    if depart is None:
        return None
    snap0 = instantane(s)
    o1 = ecrire(s, {"night": True}, "P1-1 ouverture")
    snap1 = instantane(s)
    o2 = ecrire(s, {"night": True}, "P1-2 seconde ouverture")
    c1 = ecrire(s, {"night": False}, "P1-3 fermeture")
    snap2 = instantane(s)
    c2 = ecrire(s, {"night": False}, "P1-4 seconde fermeture")
    w1, w2, w3, w4 = o1["final"], o2["final"], c1["final"], c2["final"]
    j = {
        "P1-1": {"put_ok": o1["put"]["ok"], "night_vrai": w1.get("night") is True,
                 "tg2bd_a_la_requete": a_la_requete(w1.get("tg2bd"), o1),
                 "tendb_inchange": w1.get("tendb") == depart.get("tendb"),
                 "champs_changes": champs_changes(depart, w1), "stable": o1["stable"],
                 "effets": effets_de_bord(snap0, snap1, ECRIT)},
        "P1-2": {"put_ok": o2["put"]["ok"], "night_vrai": w2.get("night") is True,
                 "tg2bd_deplace": w2.get("tg2bd") != w1.get("tg2bd"),
                 "tg2bd_a_la_seconde_requete": a_la_requete(w2.get("tg2bd"), o2),
                 "champs_changes": champs_changes(w1, w2), "stable": o2["stable"]},
        "P1-3": {"put_ok": c1["put"]["ok"], "night_faux": w3.get("night") is False,
                 "tendb_a_la_requete": a_la_requete(w3.get("tendb"), c1),
                 "tg2bd_inchange": w3.get("tg2bd") == w2.get("tg2bd"),
                 "champs_changes": champs_changes(w2, w3), "stable": c1["stable"],
                 "effets": effets_de_bord(snap1, snap2, ECRIT)},
        "P1-4": {"put_ok": c2["put"]["ok"], "night_faux": w4.get("night") is False,
                 "tendb_deplace": w4.get("tendb") != w3.get("tendb"),
                 "champs_changes": champs_changes(w3, w4), "stable": c2["stable"]},
    }
    a, f = j["P1-1"], j["P1-3"]
    j["H1_tient"] = bool(a["put_ok"] and a["night_vrai"] and a["tg2bd_a_la_requete"]
                         and a["tendb_inchange"] and not a["effets"] and a["stable"])
    j["H2_tient"] = bool(f["put_ok"] and f["night_faux"] and f["tendb_a_la_requete"]
                         and f["tg2bd_inchange"] and not f["effets"] and f["stable"])
    rel.ecrire(type="cycle", k=k, depart=depart, o1=o1, o2=o2, c1=c1, c2=c2, snap0=snap0,
               snap1=snap1, snap2=snap2, jugement=j)
    print(f"cycle {k} : H1 {'tient' if j['H1_tient'] else 'TOMBE'} "
          f"(champs {a['champs_changes']}, effets {a['effets'] or 'aucun'}) ; "
          f"P1-2 tg2bd deplace={j['P1-2']['tg2bd_deplace']} ; "
          f"H2 {'tient' if j['H2_tient'] else 'TOMBE'} "
          f"(champs {f['champs_changes']}, effets {f['effets'] or 'aucun'}) ; "
          f"P1-4 tendb deplace={j['P1-4']['tendb_deplace']}", flush=True)
    if a["effets"] or f["effets"]:
        raise Inattendu(f"cycle {k} : effet de bord hors de wungt")
    return j


def inscription(s, rel, test, champ, recul, avec_night=None, ouvrir_avant=False):
    """Ecriture directe de `champ` a l'heure du reveil moins `recul` secondes.

    avec_night : None (champ seul), True ou False (dans la meme charge que `night`).
    ouvrir_avant : ouvrir d'abord une session par un `night: true` ordinaire."""
    depart = depart_verifie(s, rel, test)
    if depart is None:
        return None
    ouverture = None
    if ouvrir_avant:
        ouverture = ecrire(s, {"night": True}, f"{test} ouverture prealable")
        if ouverture["final"].get("night") is not True:
            rel.ecrire(type="inscription", test=test, ecarte="ouverture prealable non constatee",
                       ouverture=ouverture)
            print(f"{test} : ECARTE, ouverture prealable non constatee", flush=True)
            fermee(s, rel)
            return None
    valeur_avant = (ouverture["final"] if ouverture else depart).get(champ)
    maintenant, _ = heure_appareil(s)
    if maintenant is None:
        rel.ecrire(type="inscription", test=test, ecarte="heure du reveil illisible")
        fermee(s, rel)
        return None
    cible = iso(maintenant - timedelta(seconds=recul))
    charge = {champ: cible} if avec_night is None else {"night": avec_night, champ: cible}
    snap0 = instantane(s)
    e = ecrire(s, charge, f"{test} {json.dumps(charge)}")
    snap1 = instantane(s)
    issue = classer(e, champ, cible, valeur_avant)
    effets = effets_de_bord(snap0, snap1, ECRIT)
    close = fermee(s, rel) is not None
    rel.ecrire(type="inscription", test=test, champ=champ, recul_s=recul, cible=cible,
               charge=charge, depart=depart, ouverture=ouverture, ecriture=e, issue=issue,
               night_apres=e["final"].get("night"), champs=champs_changes(
                   ouverture["final"] if ouverture else depart, e["final"]),
               effets=effets, close_a_la_fin=close)
    print(f"{test} : {'+'.join(charge)} -> put {e['put'].get('status')} "
          f"{e['put'].get('body') if not e['put']['ok'] else ''} => {issue} "
          f"(night apres={e['final'].get('night')}, effets={effets or 'aucun'})", flush=True)
    if effets:
        raise Inattendu(f"{test} : effet de bord hors de wungt")
    return issue


def tenue(s, rel):
    depart = depart_verifie(s, rel, "P1-5")
    if depart is None:
        return None
    o = ecrire(s, {"night": True}, "P1-5 ouverture")
    w0 = o["final"]
    lectures, t0 = [], time.monotonic()
    for i in range(1, TENUE_S // PAS_TENUE_S + 1):
        attendre_depuis(t0, i * PAS_TENUE_S)
        lectures.append({"t": i * PAS_TENUE_S, "wungt": wungt(s)})
    echecs = sum(1 for x in lectures if x["wungt"] is None)
    derives = [x for x in lectures if x["wungt"] is not None and x["wungt"] != w0]
    c = ecrire(s, {"night": False}, "P1-5 fermeture")
    rel.ecrire(type="tenue", ouverture=o, lectures=lectures, derives=derives, echecs=echecs,
               fermeture=c)
    print(f"P1-5 tenue {TENUE_S} s : {len(lectures)} lectures, {len(derives)} ecart(s), "
          f"{echecs} echec(s) de lecture ; close={c['final'].get('night') is False}", flush=True)
    return not derives


def restaurer(s, rel, initial, issue_tg2bd_seul, issue_tendb_seul):
    """Session close, puis heures initiales reposees si elles s'ecrivent seules."""
    limite = time.monotonic() + DUREE_RESTAURATION / ACCELERATION
    close, w = False, None
    while time.monotonic() < limite:
        w = wungt(s)
        if w is not None and w.get("night") is False:
            close = True
            break
        s.put(1, "wungt", {"night": False})
        dormir(5, interruptible=False)
    remises = []
    if close:
        for champ, issue in (("tg2bd", issue_tg2bd_seul), ("tendb", issue_tendb_seul)):
            if issue == "inscriptible" and w.get(champ) != initial.get(champ):
                remises.append(s.put(1, "wungt", {champ: initial.get(champ)}))
                dormir(1, interruptible=False)
    final = wungt(s)
    identique = final == initial
    rel.ecrire(type="restauration", session_close=close, remises=remises, final=final,
               identique_au_debut=identique)
    print(f"restauration : session {'close' if close else 'OUVERTE — A VERIFIER'} ; wungt "
          f"{'identique au debut' if identique else 'different du debut (heures de l essai)'}",
          flush=True)
    return close


def charger_reference(chemin):
    resume = None
    with open(chemin, encoding="utf-8") as fh:
        for ligne in fh:
            rec = json.loads(ligne)
            if rec.get("type") == "resume":
                resume = rec
    return resume


def controle(s, rel, ref):
    """P1-9 : la session ouverte par SleepMapper, comparee a la notre, puis fermee par nous."""
    snap = instantane(s)
    w = snap.get("1/wungt") or {}
    maintenant, _ = heure_appareil(s)
    fin_sonde, tg = lire_heure(ref.get("heure_fin")), lire_heure(w.get("tg2bd"))
    j = {"night_vrai": w.get("night") is True,
         "tg2bd_posterieur_a_la_sonde": (tg is not None and fin_sonde is not None
                                         and maintenant is not None
                                         and fin_sonde <= tg <= maintenant + TOLERANCE),
         "champs_changes_app": champs_changes(ref.get("wungt_final"), w),
         "champs_changes_sonde": ref.get("champs_ouverture"),
         "effets_hors_wungt": effets_de_bord(ref.get("instantane_final") or {}, snap, ECRIT)}
    j["memes_champs"] = j["champs_changes_app"] == j["champs_changes_sonde"]
    rel.ecrire(type="controle", instantane=snap, heure=iso(maintenant), jugement=j)
    print(f"P1-9 : session ouverte={j['night_vrai']}, tg2bd posterieur a la sonde="
          f"{j['tg2bd_posterieur_a_la_sonde']}, champs app={j['champs_changes_app']} / sonde="
          f"{j['champs_changes_sonde']} -> memes={j['memes_champs']} ; hors wungt : "
          f"{j['effets_hors_wungt'] or 'rien'}", flush=True)
    if not j["night_vrai"]:
        print("aucune session ouverte : l'appui n'a pas ete pris, rien a fermer", flush=True)
        return j
    c = ecrire(s, {"night": False}, "P1-9 fermeture d'une session ouverte par SleepMapper")
    jc = {"night_faux": c["final"].get("night") is False,
          "tendb_a_la_requete": a_la_requete(c["final"].get("tendb"), c),
          "tg2bd_inchange": c["final"].get("tg2bd") == w.get("tg2bd")}
    rel.ecrire(type="controle_fermeture", ecriture=c, jugement=jc)
    print(f"P1-9 fermeture par la sonde : {jc}", flush=True)
    return j


def main():
    p = argparse.ArgumentParser(description="P1 — les ecritures dans wungt")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--lecture", action="store_true", help="aucune ecriture")
    g.add_argument("--ecriture", action="store_true", help="P1-1 a P1-8")
    g.add_argument("--controle", action="store_true", help="P1-9, apres un appui dans l'app")
    p.add_argument("--reference", help="releve de --ecriture (pour --controle)")
    p.add_argument("--hote", help="adresse[:port] imposee (faux reveil) ; sinon SSDP")
    a = p.parse_args()
    ref = None
    if a.controle:
        if not a.reference:
            p.error("--controle exige --reference")
        ref = charger_reference(a.reference)
        if ref is None:
            p.error(f"aucun enregistrement 'resume' dans {a.reference}")
    installer_signaux()
    mode = "lecture" if a.lecture else "ecriture" if a.ecriture else "controle"
    rel = Journal(f"ecriture-wungt-{mode}-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

    def abandon(raison):
        rel.ecrire(type="fin", abandon=raison)
        print(f"ABANDON : {raison}", file=sys.stderr, flush=True)
        return 1

    pids, vivants = arreter_capture()
    rel.ecrire(type="debut", mode=mode, capture_arretee=pids, capture_vivante=vivants)
    if vivants:
        return abandon("capture toujours vivante : deux clients tomberaient ensemble")
    hote = trouver(a.hote)
    if not hote:
        return abandon("Somneo introuvable")
    s = Session(hote)
    sts = s.corps(1, "wusts") or {}
    initial = wungt(s)
    heure, _ = heure_appareil(s)
    rel.ecrire(type="initial", wusts=sts.get("wusts"), wungt=initial, heure=iso(heure))
    print(f"etat initial : wusts={sts.get('wusts')} night={(initial or {}).get('night')} "
          f"heure du reveil lue={'oui' if heure else 'NON'}", flush=True)
    if initial is None or heure is None:
        return abandon("wungt ou l'heure du reveil illisible")
    if alarme_en_cours(sts.get("wusts")):
        return abandon(f"alarme en cours ou wusts illisible ({sts.get('wusts')})")

    if a.lecture:
        rel.ecrire(type="lecture", instantane=instantane(s))
        rel.ecrire(type="fin")
        s.fermer()
        print(f"lecture seule terminee -> {rel.nom}", flush=True)
        return 0

    if a.controle:
        try:
            controle(s, rel, ref)
        except Arret:
            rel.ecrire(type="interruption")
        finally:
            lever_arret()
            rel.ecrire(type="fin")
            s.fermer()
        print(f"-> {rel.nom}", flush=True)
        return 0

    if initial.get("night") is not False:
        return abandon("une session est deja ouverte : c'est peut-etre une vraie nuit")
    issues, premier = {}, None
    try:
        for k in range(1, CYCLES + 1):                                 # P1-1 a P1-4
            j = cycle(s, rel, k)
            if j is None:
                raise Inattendu(f"cycle {k} : impossible de partir d'une session close")
            premier = premier or j
        for rep, recul in enumerate(RECULS_P16, 1):                    # P1-6
            issues[f"P1-6 #{rep}"] = inscription(s, rel, f"P1-6 #{rep}", "tg2bd", recul,
                                                 avec_night=True)
        issues["P1-7a"] = inscription(s, rel, "P1-7a", "tg2bd", RECUL, ouvrir_avant=True)
        issues["P1-7b"] = inscription(s, rel, "P1-7b", "tg2bd", RECUL)
        issues["P1-8a"] = inscription(s, rel, "P1-8a", "tendb", RECUL, avec_night=False,
                                      ouvrir_avant=True)
        issues["P1-8b"] = inscription(s, rel, "P1-8b", "tendb", RECUL)
        issues["P1-5"] = tenue(s, rel)                                 # P1-5, en dernier
    except Inattendu as exc:
        rel.ecrire(type="arret_securite", raison=str(exc))
        print(f"ARRET DE SECURITE : {exc} — plus aucune ecriture, restauration", flush=True)
    except Arret:
        rel.ecrire(type="interruption")
        print("interrompue — restauration", flush=True)
    finally:
        lever_arret()
        restaurer(s, rel, initial, issues.get("P1-7b"), issues.get("P1-8b"))
        heure_fin, _ = heure_appareil(s)
        rel.ecrire(type="resume", issues=issues,
                   champs_ouverture=((premier or {}).get("P1-1") or {}).get("champs_changes"),
                   wungt_final=wungt(s), instantane_final=instantane(s, interruptible=False),
                   heure_fin=iso(heure_fin))
        rel.ecrire(type="fin")
        s.fermer()
    print(f"issues : {issues} -> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
