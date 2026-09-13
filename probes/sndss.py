"""`sndss` (depart en douceur) — que vaut ce champ, et quelles bornes ? Sonde de refutation.

Protocole ecrit et commite AVANT la mesure (regle du depot, memoire tester-pour-refuter). Le
collecteur (relais du pilotage, increment 4) veut exposer le depart en douceur d'une alarme et du
coucher de soleil, comme SleepMaxxer le demande. Mais `sndss` n'est etabli nulle part : la
retro-ingenierie l'a liste dans `wualm/prfwu` et `wudsk` sans son sens, SleepMapper ne l'expose
pas, et une seule valeur a ete vue — `wudsk.sndss = 200`, le 2026-09-09. On mesure avant d'ecrire.

Ce qu'on cherche : le sens (une duree ? un drapeau ? une echelle ?), les bornes (quelles valeurs
`200`, quelles `422`), et si l'ecriture a un effet de bord. Tant que ce n'est pas su, le relais
n'ecrit pas `sndss` (plan technique §7).

Deux ports portent `sndss` :
  - `wualm/prfwu` : depart en douceur d'un profil d'alarme. Ecrit sur un profil DORMANT
    (desactive et invisible), jamais sur une alarme visible ou armee — comme la sonde P3.
  - `wudsk` : depart en douceur du coucher de soleil. Ecrit avec le coucher de soleil ETEINT
    (`onoff` relu false), la valeur restauree ensuite.

Hypotheses, et ce qui les ferait tomber :
  H1  `sndss` accepte une plage de valeurs (`200`) et en refuse d'autres (`422`). Tombe si tout
      passe, ou si tout est refuse (champ non inscriptible, comme `tg2bd`).
  H2  Une valeur envoyee est reprise a la relecture (le champ s'ecrit vraiment). Tombe si le
      `200` ne prend pas la valeur — le « accepte mais ignore » deja vu sur `tg2bd`, `tmser`.
  H3  Ecrire `sndss` seul ne touche a rien d'autre : ni `onoff`, ni le son, ni un autre port.

Tests, tous depuis un etat verifie, releve a chaque etape :
  S-1  etat lu deux fois sans ecriture. S'il bouge seul, le diff ne vaut rien : abandon.
  S-2  `wualm/prfwu.sndss` d'un profil dormant : valeurs candidates {0, 1, 100, 200, 255, 300},
       chacune ecrite, relue a +0,5 s, comparee ; instantane large avant/apres ; retour a la
       valeur de depart entre chaque.
  S-3  `wudsk.sndss`, coucher de soleil eteint : memes valeurs, meme protocole.
  S-4  hors bornes, une fois la plage entrevue : la premiere valeur refusee et la premiere prise.

Au premier effet de bord ou premier refus de restauration, la sonde CESSE d'ecrire (Inattendu) :
repeter une ecriture a effet de bord sur un appareil en service, ce serait l'aggraver. A la fin,
restauration de `sndss` a sa valeur de depart sur les deux ports, reessayee jusqu'a conformite —
et ABANDONNEE si l'ecriture est refusee (une boucle de restauration qui insiste sur un refus
n'aboutit jamais : les 489 PUT de `ecriture_heure.py`, AGENTS.md).

`--lecture` : S-1 seulement, plus `sndss` des 16 profils et de `wudsk`, aucune ecriture.
`--ecriture` : tout.

ECRITURE : `wualm/prfwu.sndss` (profil dormant) et `wudsk.sndss` (coucher eteint). Jamais un
profil visible, jamais la lumiere, jamais une alarme en cours, jamais `fac`. Abandon si une alarme
sonne ou est en rappel, ou si le profil dormant ne se trouve pas.

Arrete `capture.py` au demarrage (SIGTERM par PID) ; le superviseur la relance a la fin. Releve
brut : il porte les heures d'alarme (`prfwu`, `aalms`). NE PAS le verser tel quel dans results/ —
n'en garder que statuts, valeurs de `sndss`, et listes de champs changes.
"""
import argparse
import sys
import time

from somneo_session import (Arret, Inattendu, Journal, Session, alarme_en_cours, arreter_capture,
                            difference, dormir, effets_de_bord, instantane, installer_signaux,
                            lever_arret, trouver, verifier)

CANDIDATES = [0, 1, 100, 200, 255, 300]
DUREE_RESTAURATION = 600


def selectionner(s, n):
    return s.put(1, "wualm", {"prfnr": n})


def sndss_profil(s, n):
    """Selectionne le profil n et rend son `sndss` (et le corps complet, pour le diff)."""
    selectionner(s, n)
    dormir(0.3)
    corps = s.corps(1, "wualm/prfwu") or {}
    return corps.get("sndss"), corps


def profil_dormant(s):
    """Un profil desactive ET invisible (aucune alarme ne peut en dependre), n >= 3."""
    envs = s.corps(1, "wualm/aenvs") or {}
    prfen, prfvs = envs.get("prfen") or [], envs.get("prfvs") or []
    return next((n for n in range(3, 17) if len(prfen) >= n and len(prfvs) >= n
                 and prfen[n - 1] is False and prfvs[n - 1] is False), None)


def essai_port(s, rel, ecrire, relire, valeur, depart, test):
    """Ecrit `valeur`, relit, juge : pris ? refuse ? effet de bord ? Rend (pris, refuse, effet)."""
    verifier()
    large0 = instantane(s)
    put = ecrire(valeur)
    dormir(0.5)
    apres, corps = relire()
    large1 = instantane(s)
    pris = put["ok"] and apres == valeur
    refuse = not put["ok"]
    # ports ecrits prefixes du produit, comme les cles de l'instantane (1/wualm...) : sinon le
    # champ qu'on vient d'ecrire est pris pour un effet de bord (modele : selection_profil.py).
    effets = effets_de_bord(large0, large1, ports_ecrits=("1/wualm", "1/wualm/prfwu", "1/wudsk"))
    rel.ecrire(type=test, valeur=valeur, put_status=put.get("status"), put_ok=put["ok"],
               sndss_relu=apres, pris=pris, refuse=refuse, effets=effets)
    print(f"{test} sndss={valeur:>3} : put {put.get('status')} relu={apres} "
          f"{'PRIS' if pris else ('refuse' if refuse else 'accepte mais ignore')} "
          f"effets={'aucun' if not effets else [e[0] for e in effets]}", flush=True)
    return pris, refuse, bool(effets)


def restaurer_champ(s, rel, ecrire, relire, cible, nom):
    """Reecrit `cible` jusqu'a la relire, 10 min au plus. Abandonne si l'ecriture est REFUSEE."""
    lever_arret()
    limite = time.monotonic() + DUREE_RESTAURATION
    while True:
        put = ecrire(cible)
        dormir(1.0, interruptible=False)
        valeur, _ = relire()
        if not put["ok"]:
            rel.ecrire(type="restauration", port=nom, abandon="ecriture refusee", put=put)
            print(f"restauration {nom} : ABANDON, ecriture refusee ({put.get('status')}) — "
                  f"NE PAS INSISTER, verifier a la main", flush=True)
            return False
        if valeur == cible or time.monotonic() > limite:
            ok = valeur == cible
            rel.ecrire(type="restauration", port=nom, conforme=ok, cible=cible, relu=valeur)
            print(f"restauration {nom} : {'sndss rendu a ' + str(cible) if ok else 'NON CONFORME — A VERIFIER'}",
                  flush=True)
            return ok
        dormir(10, interruptible=False)


def main():
    p = argparse.ArgumentParser(description="sndss — sens et bornes du depart en douceur")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--lecture", action="store_true", help="S-1 et l'etat, aucune ecriture")
    g.add_argument("--ecriture", action="store_true", help="S-1 a S-4")
    p.add_argument("--hote", help="adresse[:port] imposee (faux reveil) ; sinon SSDP")
    a = p.parse_args()
    installer_signaux()
    mode = "lecture" if a.lecture else "ecriture"
    rel = Journal(f"sndss-{mode}-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

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

    e1 = instantane(s)
    dormir(2)
    e2 = instantane(s)
    stable = difference(e1, e2) == []
    dsk = s.corps(1, "wudsk") or {}
    rel.ecrire(type="S-1", stable=stable, ecart=difference(e1, e2),
               wudsk_sndss=dsk.get("sndss"), wudsk_onoff=dsk.get("onoff"))
    print(f"S-1 etat stable sans ecriture : {stable} ; wudsk.sndss={dsk.get('sndss')} "
          f"onoff={dsk.get('onoff')}", flush=True)
    if not stable:
        return abandon("l'etat bouge sans ecriture : le diff ne vaudrait rien")

    profils_sndss = {n: sndss_profil(s, 1 if a.lecture else n)[0] for n in range(1, 17)} \
        if a.lecture else {}
    if a.lecture:
        rel.ecrire(type="lecture", profils_sndss=profils_sndss, wudsk_sndss=dsk.get("sndss"))
        print(f"sndss par profil : {profils_sndss}", flush=True)
        rel.ecrire(type="fin")
        s.fermer()
        rel.fermer()
        return 0

    dormant = profil_dormant(s)
    if dormant is None:
        return abandon("aucun profil dormant (desactive et invisible) : rien ou ecrire sans risque")
    initial_profil = prfnr_initial = (s.corps(1, "wualm") or {}).get("prfnr")
    depart_profil, _ = sndss_profil(s, dormant)
    depart_dsk = dsk.get("sndss")
    if dsk.get("onoff"):
        return abandon("coucher de soleil allume : ne pas ecrire wudsk, relancer eteint")
    rel.ecrire(type="choix", dormant=dormant, profil_initial=initial_profil,
               sndss_profil_depart=depart_profil, sndss_wudsk_depart=depart_dsk)
    print(f"profil dormant={dormant} (sndss={depart_profil}), wudsk.sndss={depart_dsk}, "
          f"profil initial={initial_profil}", flush=True)

    def ecrire_profil(v):
        return s.put(1, "wualm/prfwu", {"prfnr": dormant, "sndss": v})

    def relire_profil():
        return sndss_profil(s, dormant)

    def ecrire_dsk(v):
        return s.put(1, "wudsk", {"sndss": v})

    def relire_dsk():
        c = s.corps(1, "wudsk") or {}
        return c.get("sndss"), c

    # Tout ce qui ecrit est sous try : un SIGTERM (Arret) ou un effet de bord (Inattendu) mene a
    # la restauration, jamais a une sortie qui laisserait sndss sur une valeur d'essai.
    arrete = False
    try:
        for test, ecrire, relire, depart in (("S-2", ecrire_profil, relire_profil, depart_profil),
                                             ("S-3", ecrire_dsk, relire_dsk, depart_dsk)):
            if depart is None:
                rel.ecrire(type=test, ignore="sndss absent au depart")
                continue
            for v in CANDIDATES:
                verifier()
                pris, refuse, effet = essai_port(s, rel, ecrire, relire, v, depart, test)
                # retour a la valeur de depart entre chaque essai (sauf si refuse : rien n'a bouge)
                if pris:
                    ecrire(depart)
                    dormir(0.5)
                if effet:
                    raise Inattendu(f"{test} sndss={v}")
    except Arret:
        rel.ecrire(type="arret", raison="SIGTERM/SIGINT recu pendant la mesure")
        print("arret demande — restauration", flush=True)
        arrete = True
    except Inattendu as exc:
        rel.ecrire(type="inattendu", detail=str(exc))
        print(f"effet de bord ({exc}) — arret des ecritures, restauration", flush=True)
        arrete = True

    # restauration : selection du profil initial, sndss des deux ports a leur valeur de depart
    r1 = restaurer_champ(s, rel, ecrire_profil, relire_profil, depart_profil, "wualm/prfwu.sndss") \
        if depart_profil is not None else True
    r2 = restaurer_champ(s, rel, ecrire_dsk, relire_dsk, depart_dsk, "wudsk.sndss") \
        if depart_dsk is not None else True
    if prfnr_initial is not None:
        selectionner(s, prfnr_initial)
    rel.ecrire(type="fin", restauration_profil=r1, restauration_wudsk=r2, arrete_avant_la_fin=arrete)
    s.fermer()
    rel.fermer()
    return 0 if (r1 and r2) else 1


if __name__ == "__main__":
    sys.exit(main())
