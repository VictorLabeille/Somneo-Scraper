"""P3 — Lire un profil d'alarme ne change-t-il rien ? Sonde de refutation, 2026-09-12.

Protocole ecrit et commite AVANT la mesure (plan technique du collecteur, §1, P3).

`pysomneo` lit le detail d'un profil en le selectionnant : `PUT wualm {"prfnr": n}`
(`modify_alarm_details`, appele par `get_alarm_details`). La racine `wualm` porte alors
`prfnr` = n et le detail du profil n dans `prfwu`. A ne pas confondre avec `PUT wualm/prfwu`,
qui CONFIGURE un profil. Le collecteur fera cette lecture pour les seize profils, une fois par
jour. C'est une ecriture, et sur les alarmes : on verifie qu'elle ne touche a rien d'autre
avant de s'en servir.

Hypotheses, et ce qui les ferait tomber :
  H8  Selectionner un profil ne change que `prfnr` et le profil servi dans `prfwu`.
      Tombe si `aenvs`, `aalms`, `alctr` ou `snztm` bougent, si un autre port bouge, ou si le
      profil lu differe d'une passe a l'autre.
  H9  Le profil selectionne se lit sans autre ecriture : dans la reponse du PUT, ou dans
      `wualm` et `wualm/prfwu` relus ensuite. Les trois sont compares.

Tests :
  P3-1  arbre `wualm` lu deux fois sans ecriture. S'il bouge seul, le diff ne vaut rien :
        abandon.
  P3-2  selection d'un profil dormant (desactive et invisible) x3, chacune depuis l'etat
        initial verifie : arbre relu a +0,5 s et +5 s, instantane large avant et apres.
  P3-3  a chaque selection : reponse du PUT, `wualm.prfwu` et `wualm/prfwu` compares.
  P3-4  profil 2 (visible, desactive), puis le profil initial (celui de l'alarme active).
  P3-5  l'operation reelle du collecteur : profils 1 a 16, deux passes, passes comparees, et
        le profil initial compare a celui lu au depart.
  P3-6  hors bornes, `prfnr` 0 puis 17 — seulement si tout ce qui precede est conforme.
  P3-7  (a la main) ouvrir le menu des alarmes sur l'appareil apres la sonde.

Apres chaque selection, retour au profil initial et relecture de l'arbre entier : l'essai
suivant repart d'un etat verifie. Au premier resultat non conforme, la sonde cesse d'ecrire :
repeter une ecriture qui a un effet de bord, ce serait l'aggraver. A la fin, restauration
reessayee jusqu'a ce que l'arbre soit identique, champ pour champ, a celui du debut.

`--lecture` : P3-1 seulement, aucune ecriture. `--ecriture` : tout. Sans l'un des deux, rien.

ECRITURE : `wualm`, champ `prfnr` uniquement. Jamais `wualm/prfwu`, jamais `aenvs`, `aalms`,
`alctr`, jamais la lumiere, jamais `fac`. Abandon si une alarme est en cours.

Arrete `capture.py` au demarrage (SIGTERM par PID) ; le superviseur la relance a la fin.
Releve brut : il contient les heures d'alarme (`aalms`, `prfwu`). NE PAS le verser tel quel
dans probes/results/ — n'en garder que statuts, durees et listes de champs modifies.
"""
import argparse
import sys
import time

from somneo_session import (ACCELERATION, Arret, Inattendu, Journal, Session,
                            alarme_en_cours, arreter_capture, difference, dormir,
                            effets_de_bord, instantane, installer_signaux, lever_arret, trouver,
                            verifier)

INVARIANTS = ("aenvs", "aalms", "alctr", "snztm")
DUREE_RESTAURATION = 600


def arbre(s):
    return s.corps(1, "wualm")


def ecart(a, b):
    """Diff de deux arbres `wualm`, champ par champ."""
    return difference({"wualm": a}, {"wualm": b}, ignorer=())


def selectionner(s, n):
    return s.put(1, "wualm", {"prfnr": n})


def hors_selection(changes):
    """Les changements qui ne sont ni `prfnr` ni le profil servi dans `prfwu`."""
    return [c for c in changes
            if not (c[0] == "wualm.prfnr" or c[0].startswith("wualm.prfwu"))]


def revenir(s, initial, base):
    put = selectionner(s, initial)
    dormir(0.5)
    fin = arbre(s)
    return put, fin, fin == base


def essai(s, rel, base, initial, n, test, rep):
    verifier()
    depart = arbre(s)
    if depart != base:
        rel.ecrire(type="essai", test=test, rep=rep, n=n,
                   ecarte="depart different de l'etat initial", ecart=ecart(base, depart))
        print(f"{test} #{rep} profil {n} : ECARTE, depart non conforme", flush=True)
        return False
    large0 = instantane(s)
    put = selectionner(s, n)
    dormir(0.5)
    relu_05 = arbre(s)
    prfwu_get = s.corps(1, "wualm/prfwu")
    dormir(4.5)
    relu_5 = arbre(s)
    large1 = instantane(s)
    changes = ecart(base, relu_5) if relu_5 is not None else None
    inattendus = hors_selection(changes) if changes is not None else None
    effets = effets_de_bord(large0, large1, ports_ecrits=("1/wualm",))
    profil = (relu_05 or {}).get("prfwu")
    reponse = put.get("body")
    j = {
        "put_ok": put["ok"],
        "prfnr_relu": (relu_05 or {}).get("prfnr"),
        "prfnr_conforme": (relu_05 or {}).get("prfnr") == n,
        "stable_05_5": relu_05 is not None and relu_05 == relu_5,
        "invariants_intacts": inattendus == [],
        "sans_effet_de_bord": not effets,
        "reponse_put_cles": sorted(reponse) if isinstance(reponse, dict) else None,
        "reponse_put_egale_profil": reponse == profil,
        "get_prfwu_egal_profil": prfwu_get == profil,
    }
    retour, fin, retour_ok = revenir(s, initial, base)
    conforme = (j["put_ok"] and j["prfnr_conforme"] and j["stable_05_5"]
                and j["invariants_intacts"] and j["sans_effet_de_bord"])
    rel.ecrire(type="essai", test=test, rep=rep, n=n, put=put, relu_05=relu_05, relu_5=relu_5,
               prfwu_get=prfwu_get, changes=changes, inattendus=inattendus, effets=effets,
               jugement=j, retour=retour, retour_conforme=retour_ok, conforme=conforme)
    print(f"{test} #{rep} profil {n:>2} : put {put.get('status')} prfnr={j['prfnr_relu']} "
          f"stable={j['stable_05_5']} "
          f"invariants={'ok' if j['invariants_intacts'] else 'MODIFIES ' + str(inattendus)} "
          f"effets={'aucun' if not effets else effets} "
          f"reponse=profil:{j['reponse_put_egale_profil']} "
          f"get=profil:{j['get_prfwu_egal_profil']} "
          f"retour={'ok' if retour_ok else 'NON CONFORME'}", flush=True)
    return conforme and retour_ok


def passe(s, rel, base, initial, k):
    profils, anomalies = {}, []
    for n in range(1, 17):
        verifier()
        put = selectionner(s, n)
        dormir(0.3)
        a = arbre(s)
        if (not put["ok"] or a is None or a.get("prfnr") != n
                or any(a.get(c) != base.get(c) for c in INVARIANTS)):
            anomalies.append({"n": n, "put": put, "arbre": a})
        profils[n] = (a or {}).get("prfwu")
    retour, fin, retour_ok = revenir(s, initial, base)
    rel.ecrire(type="passe", k=k, profils=profils, anomalies=anomalies, retour=retour,
               retour_conforme=retour_ok)
    print(f"P3-5 passe {k} : 16 selections, {len(anomalies)} anomalie(s)"
          f"{' ' + str([x['n'] for x in anomalies]) if anomalies else ''}, "
          f"retour={'ok' if retour_ok else 'NON CONFORME'}", flush=True)
    return profils, anomalies, retour_ok


def hors_bornes(s, rel, base, initial):
    for n in (0, 17):
        verifier()
        put = selectionner(s, n)
        dormir(0.5)
        a = arbre(s)
        retour, fin, retour_ok = revenir(s, initial, base)
        rel.ecrire(type="hors_bornes", n=n, put=put, arbre=a,
                   ecart=ecart(base, a) if a is not None else None, retour=retour,
                   retour_conforme=retour_ok)
        print(f"P3-6 prfnr={n} : put {put.get('status')} {put.get('body') or put.get('raw') or ''}"
              f" prfnr relu={(a or {}).get('prfnr')} "
              f"retour={'ok' if retour_ok else 'NON CONFORME'}", flush=True)
        if not retour_ok:
            return False
    return True


def restaurer(s, rel, base, initial):
    """Reselectionne le profil initial jusqu'a un arbre identique a celui du debut, 10 min au plus."""
    limite = time.monotonic() + DUREE_RESTAURATION / ACCELERATION
    while True:
        put = selectionner(s, initial)
        dormir(1.0, interruptible=False)
        a = arbre(s)
        ok = a == base
        if ok or time.monotonic() > limite:
            rel.ecrire(type="restauration", put=put, conforme=ok,
                       ecart=ecart(base, a) if a is not None else None)
            print(f"restauration : "
                  f"{'arbre identique au debut' if ok else 'NON CONFORME — A VERIFIER'}",
                  flush=True)
            return ok
        dormir(10, interruptible=False)


def main():
    p = argparse.ArgumentParser(description="P3 — la selection d'un profil d'alarme")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--lecture", action="store_true", help="P3-1 seulement, aucune ecriture")
    g.add_argument("--ecriture", action="store_true", help="P3-1 a P3-6")
    p.add_argument("--hote", help="adresse[:port] imposee (faux reveil) ; sinon SSDP")
    a = p.parse_args()
    installer_signaux()
    mode = "lecture" if a.lecture else "ecriture"
    rel = Journal(f"selection-profil-{mode}-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

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
    if alarme_en_cours(sts.get("wusts")):
        return abandon(f"alarme en cours ou wusts illisible ({sts.get('wusts')})")
    b1 = arbre(s)
    dormir(2)
    b2 = arbre(s)
    stable = b1 is not None and b1 == b2
    rel.ecrire(type="P3-1", stable=stable, ecart=ecart(b1, b2) if b1 and b2 else None, arbre=b1,
               wusts=sts.get("wusts"))
    print(f"P3-1 arbre wualm stable sans ecriture : {stable} ; wusts={sts.get('wusts')}",
          flush=True)
    if not stable:
        return abandon("l'arbre wualm bouge sans ecriture : le diff ne vaudrait rien")
    initial = b1.get("prfnr")
    envs = b1.get("aenvs") or {}
    prfen, prfvs = envs.get("prfen") or [], envs.get("prfvs") or []
    dormant = next((n for n in range(3, 17) if len(prfen) >= n and len(prfvs) >= n
                    and prfen[n - 1] is False and prfvs[n - 1] is False), None)
    rel.ecrire(type="choix", initial=initial, dormant=dormant,
               profil2={"prfen": prfen[1] if len(prfen) > 1 else None,
                        "prfvs": prfvs[1] if len(prfvs) > 1 else None})
    print(f"profil initial={initial}, dormant retenu={dormant}", flush=True)

    if a.lecture:
        rel.ecrire(type="lecture", prfwu_get=s.corps(1, "wualm/prfwu"), instantane=instantane(s))
        rel.ecrire(type="fin")
        s.fermer()
        print(f"lecture seule terminee -> {rel.nom}", flush=True)
        return 0
    if not isinstance(initial, int) or dormant is None:
        return abandon(f"profil initial ({initial}) ou profil dormant ({dormant}) introuvable")

    bilan = False
    try:
        for n, test, rep in ((dormant, "P3-2", 1), (dormant, "P3-2", 2), (dormant, "P3-2", 3),
                             (2, "P3-4", 1), (initial, "P3-4", 2)):   # P3-2, P3-3, P3-4
            if not essai(s, rel, b1, initial, n, test, rep):
                raise Inattendu(f"{test} #{rep} (profil {n}) non conforme")
        pa, an1, r1 = passe(s, rel, b1, initial, 1)                   # P3-5
        if an1 or not r1:
            raise Inattendu("P3-5 passe 1 non conforme")
        pb, an2, r2 = passe(s, rel, b1, initial, 2)
        if an2 or not r2:
            raise Inattendu("P3-5 passe 2 non conforme")
        differents = [n for n in range(1, 17) if pa.get(n) != pb.get(n)]
        coherent = pa.get(initial) == b1.get("prfwu")
        rel.ecrire(type="P3-5", passes_egales=not differents, differents=differents,
                   profil_initial_coherent=coherent)
        print(f"P3-5 passes identiques : {not differents}"
              f"{' (differents : ' + str(differents) + ')' if differents else ''} ; "
              f"profil initial identique a celui du depart : {coherent}", flush=True)
        if differents or not coherent:
            raise Inattendu("P3-5 : lire les profils les altere")
        bilan = hors_bornes(s, rel, b1, initial)                      # P3-6
    except Inattendu as exc:
        rel.ecrire(type="arret_securite", raison=str(exc))
        print(f"ARRET DE SECURITE : {exc} — plus aucune ecriture, restauration", flush=True)
    except Arret:
        bilan = False
        rel.ecrire(type="interruption")
        print("interrompue — restauration", flush=True)
    finally:
        lever_arret()
        restaure = restaurer(s, rel, b1, initial)
        rel.ecrire(type="fin", bilan=bilan, restaure=restaure)
        s.fermer()
    print(f"bilan : {'CONFORME' if bilan else 'NON CONFORME — voir le releve'} -> {rel.nom}",
          flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
