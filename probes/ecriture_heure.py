"""P2 — Peut-on remettre le reveil a l'heure ? Sonde de refutation, 2026-09-12.

Protocole ecrit et commite AVANT la mesure (plan technique du collecteur, §1, P2).

Coupe du cloud, le reveil n'a plus de source de temps (`tmsrc` = irq, `tmser` =
http://www.noserver.com) : le collecteur devra le remettre a l'heure (cadrage §2.E) et poser la
bascule saisonniere. Tout repose sur une ecriture de `products/0/time` que personne n'a mesuree
ici.

Le piege : reecrire l'heure identique ne prouve rien — une ecriture ignoree ressemble a une
ecriture appliquee. La parade, sans effet visible : mesurer le DECALAGE de l'horloge du reveil
par rapport a celle de la carte (synchronisee NTP) a quelques centiemes pres, puis le deplacer
de moins d'une seconde. Le reveil ne donne l'heure qu'a la seconde ; mais en le lisant en boucle
sur une connexion reutilisee, on voit l'instant ou sa seconde change, a +-40 ms. A cet instant,
l'heure du reveil vaut exactement la nouvelle seconde : decalage = cette seconde - l'heure de la
carte au meme instant.

Hypotheses, et ce qui les ferait tomber :
  H5  `PUT products/0/time {"datetime": ...}` est accepte ET applique, au port `time` comme a
      l'horloge decomposee `wutim`. Tombe si le decalage mesure ne bouge pas de ce qu'on a
      ecrit (a 0,15 s pres), ou si `time` bouge et pas `wutim`.
  H6  Les autres champs de `time` (timezone, dst, dstoffset, dstchangeover, calday) et de
      `wutms` (tzhrm, dstwu, tmsrc, tmsyn, tmupd) acceptent l'ecriture. `422` = non.
  H7  Le reveil lit le decalage horaire de la valeur ecrite : la meme heure ecrite en UTC (Z)
      ou sans decalage donne le meme instant. Tombe si l'horloge saute d'une ou deux heures.

Tests :
  P2-1  decalage mesure x5, sans ecriture. Dispersion > 0,1 s : la methode ne vaut rien,
        abandon. Decalage de `wutim` mesure aussi.
  P2-2  chaque champ reecrit a sa valeur actuelle, un par un ; relu ; decalage remesure.
  P2-6  `dstchangeover` deplace d'un jour, relu, restaure, relu.
  P2-8  controle positif grossier : horloge reculee de 60 s, mesuree, remise.
  P2-5  la bonne heure ecrite en UTC (Z), puis sans decalage ; horloge remise apres chacune.
  P2-3  horloge deplacee de -0,5 s, x3 ; decalages de `time` et de `wutim` remesures.
  P2-4  remise au decalage d'origine apres chaque deplacement : ecart residuel.
  P2-7  le troisieme deplacement laisse en place 1 h, decalage mesure toutes les 5 min : s'il
        revient seul, le cloud resynchronise.
  P2-9  instantane large avant et apres : une ecriture de l'heure ne doit rien toucher d'autre.

Arret de securite : si une reecriture a l'identique change un champ (P2-2), si
`dstchangeover` ne se restaure pas ou entraine un autre champ (P2-6), ou si un port hors de
`time` et `wutms` a bouge apres ces deux tests, la sonde cesse d'ecrire et restaure — avant
de toucher a l'horloge elle-meme.

Restauration : horloge remise au decalage d'origine mesure en P2-1 — pas a l'heure de la
carte : on rend l'appareil tel qu'on l'a trouve —, reessayee jusqu'a un ecart < 0,15 s ; les
champs de `time` et de `wutms` a leur valeur d'origine.

`--lecture` : P2-1 seulement, aucune ecriture. `--ecriture` : tout.

ECRITURE : `products/0/time` et `wutms` uniquement. Jamais `wualm`, jamais la lumiere, jamais
`fac`. Abandon si une alarme est en cours, ou si l'horloge de la carte n'est pas synchronisee :
toute la mesure repose sur elle. Effet visible : l'afficheur a une minute d'ecart (P2-8), ou une
a deux heures (P2-5, si le decalage est mal lu), pendant quelques secondes.

Arrete `capture.py` au demarrage (SIGTERM par PID) ; le superviseur la relance a la fin.
"""
import argparse
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

from somneo_session import (ACCELERATION, Arret, Inattendu, Journal, Session, alarme_en_cours,
                            arreter_capture, attendre_depuis, dormir, effets_de_bord,
                            heure_appareil, instantane, installer_signaux, lever_arret,
                            lire_heure, trouver, verifier)

CHAMPS_TIME = ("timezone", "dst", "dstoffset", "dstchangeover", "calday")
CHAMPS_WUTMS = ("tzhrm", "dstwu", "tmsrc", "tmsyn", "tmupd")
ESPACEMENT_MESURE = 0.05     # s : ~12 lectures par seconde pendant 2 a 3 s, sequentielles
MESURES_P21 = 5
BRUIT_MAX = 0.1
TOLERANCE = 0.15
DEPLACEMENT = -0.5
RECUL_GROSSIER = -60.0
DUREE_P27 = 3600
PAS_P27 = 300
DUREE_RESTAURATION = 1800
ECRITS = ("0/time", "1/wutms")


def seconde(s, port, tz):
    """Une lecture de l'horloge du reveil : (seconde en epoch, milieu de la requete, carte)."""
    if port == "time":
        rec = s.get(0, "time")
        b = rec.get("body") if rec["ok"] else None
        dt = lire_heure(b.get("datetime")) if isinstance(b, dict) else None
    else:
        rec = s.get(1, "wutim")
        b = rec.get("body") if rec["ok"] else None
        try:
            dt = datetime(b["yrltm"], b["moltm"], b["dtltm"], b["hrltm"], b["miltm"],
                          b["scltm"], tzinfo=tz)
        except (TypeError, KeyError, ValueError):
            dt = None
    return (int(dt.timestamp()) if dt is not None else None), (rec["t_envoi"] + rec["t_recu"]) / 2


def decalage(s, port, tz, bascules=2, limite=8.0, interruptible=True):
    """Horloge du reveil - horloge de la carte, en s, prise aux instants ou la seconde change."""
    ancien, s.espacement = s.espacement, ESPACEMENT_MESURE
    estimations = []
    try:
        prec = seconde(s, port, tz)
        fin = time.monotonic() + limite
        while len(estimations) < bascules and time.monotonic() < fin:
            if interruptible:
                verifier()
            cur = seconde(s, port, tz)
            if prec[0] is not None and cur[0] is not None and cur[0] == prec[0] + 1:
                estimations.append({"decalage": round(cur[0] - (prec[1] + cur[1]) / 2, 4),
                                    "incertitude": round((cur[1] - prec[1]) / 2, 4)})
            prec = cur
    finally:
        s.espacement = ancien
    if not estimations:
        return None, estimations
    return statistics.median(e["decalage"] for e in estimations), estimations


def ecart_grossier(s):
    """Decalage a la seconde pres, en une lecture : pour voir un saut d'une minute ou d'une heure."""
    dt, rec = heure_appareil(s)
    return None if dt is None else round(dt.timestamp() - (rec["t_envoi"] + rec["t_recu"]) / 2, 1)


def texte_heure(sec, tz, forme):
    if forme == "utc":
        return datetime.fromtimestamp(sec, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = datetime.fromtimestamp(sec, tz)
    return d.replace(tzinfo=None).isoformat() if forme == "naif" else d.isoformat()


def poser(s, cible, tz, forme="local", interruptible=True):
    """Ecrit `datetime` pour que l'horloge du reveil vaille : heure de la carte + `cible`.

    La valeur ecrite est une seconde entiere ; l'envoi est cale pour qu'elle arrive a
    l'appareil au moment ou elle est juste, compte tenu de la moitie de l'aller-retour."""
    if interruptible:
        verifier()
    rec0 = s.get(0, "time")                          # garde la connexion ouverte, mesure l'aller
    aller = (rec0.get("ms") or 40.0) / 2000.0
    base = time.time() + 0.3
    t_envoi = base + (1.0 - (base + aller + cible) % 1.0) % 1.0
    valeur = round(t_envoi + aller + cible)
    while True:
        reste = t_envoi - time.time()
        if reste <= 0:
            break
        time.sleep(reste - 0.002 if reste > 0.003 else 0)
    texte = texte_heure(valeur, tz, forme)
    put = s.put(0, "time", {"datetime": texte})
    return {"cible": cible, "forme": forme, "texte": texte, "aller_estime": round(aller, 4),
            "retard_envoi": round(put["t_envoi"] - t_envoi, 4), "put": put}


def remettre(s, origine, tz, essais=3, interruptible=True):
    """Pose le decalage d'origine et le verifie ; jusqu'a `essais` tentatives."""
    etapes = []
    for _ in range(essais):
        e = poser(s, origine, tz, interruptible=interruptible)
        dormir(1, interruptible=interruptible)
        d, _ = decalage(s, "time", tz, interruptible=interruptible)
        etapes.append({"ecriture": e, "decalage": d})
        if d is not None and abs(d - origine) < TOLERANCE:
            return {"conforme": True, "residuel": round(d - origine, 4), "etapes": etapes}
    return {"conforme": False, "residuel": None if d is None else round(d - origine, 4),
            "etapes": etapes}


def garde_alarme(s):
    """Avant chaque groupe d'ecritures : une alarme qui demarre arrete tout."""
    sts = s.corps(1, "wusts") or {}
    if alarme_en_cours(sts.get("wusts")):
        raise Arret()


def egal_heure(a, b):
    return a == b or (lire_heure(a) is not None and lire_heure(a) == lire_heure(b))


def verdict(delta, attendu):
    if delta is None:
        return "non mesure"
    if abs(delta - attendu) < TOLERANCE:
        return "applique"
    if abs(delta) < TOLERANCE:
        return "ignore"
    return "inattendu"


def p22(s, rel, tz, time0, wutms0):
    res = []
    for produit, port, champs, ref in ((0, "time", CHAMPS_TIME, time0),
                                       (1, "wutms", CHAMPS_WUTMS, wutms0)):
        garde_alarme(s)
        for c in champs:
            verifier()
            if c not in ref:
                res.append({"port": port, "champ": c, "absent": True})
                continue
            put = s.put(produit, port, {c: ref[c]})
            dormir(0.5)
            apres = s.corps(produit, port) or {}
            r = {"port": port, "champ": c, "valeur": ref[c], "put": put, "relu": apres.get(c),
                 "inchange": apres.get(c) == ref[c],
                 "autres_inchanges": all(apres.get(k) == ref.get(k) for k in champs if k != c)}
            res.append(r)
            print(f"P2-2 {port}.{c} = {ref[c]!r} -> {put.get('status')} "
                  f"{put.get('body') if not put['ok'] else ''} relu inchange={r['inchange']} "
                  f"autres inchanges={r['autres_inchanges']}", flush=True)
    d, est = decalage(s, "time", tz)
    rel.ecrire(type="P2-2", resultats=res, decalage_apres=d, estimations=est)
    if any(not r["inchange"] or not r["autres_inchanges"] for r in res if not r.get("absent")):
        raise Inattendu("P2-2 : une reecriture a l'identique a change un champ")
    return d


def p26(s, rel, time0):
    garde_alarme(s)
    orig = time0.get("dstchangeover")
    d = lire_heure(orig)
    if d is None:
        rel.ecrire(type="P2-6", saute=f"dstchangeover illisible ({orig!r})")
        print(f"P2-6 saute : dstchangeover illisible ({orig!r})", flush=True)
        return
    nouveau = (d + timedelta(days=1)).isoformat()
    put1 = s.put(0, "time", {"dstchangeover": nouveau})
    dormir(0.5)
    t1 = s.corps(0, "time") or {}
    put2 = s.put(0, "time", {"dstchangeover": orig})
    dormir(0.5)
    t2 = s.corps(0, "time") or {}
    j = {"applique": egal_heure(t1.get("dstchangeover"), nouveau),
         "restaure": egal_heure(t2.get("dstchangeover"), orig),
         "autres_inchanges_pendant": all(t1.get(k) == time0.get(k) for k in CHAMPS_TIME
                                         if k != "dstchangeover")}
    rel.ecrire(type="P2-6", nouveau=nouveau, put=put1, relu=t1, put_restauration=put2,
               relu_apres=t2, jugement=j)
    print(f"P2-6 dstchangeover +1 jour -> {put1.get('status')} applique={j['applique']} ; "
          f"restaure -> {put2.get('status')} {j['restaure']} ; autres champs inchanges="
          f"{j['autres_inchanges_pendant']}", flush=True)
    if not j["restaure"] or not j["autres_inchanges_pendant"]:
        raise Inattendu("P2-6 : dstchangeover non restaure, ou un autre champ a bouge")


def p28(s, rel, tz, origine):
    garde_alarme(s)
    e = poser(s, origine + RECUL_GROSSIER, tz)
    dormir(1)
    g = ecart_grossier(s)
    d, est = decalage(s, "time", tz)
    v = verdict(None if d is None else d - origine, RECUL_GROSSIER)
    r = remettre(s, origine, tz)
    rel.ecrire(type="P2-8", ecriture=e, grossier=g, decalage=d, estimations=est, verdict=v,
               remise=r)
    print(f"P2-8 recul de 60 s -> {e['put'].get('status')} decalage "
          f"{None if d is None else round(d - origine, 3)} s => {v} ; remise "
          f"{'conforme' if r['conforme'] else 'NON CONFORME'} (residuel {r['residuel']} s)",
          flush=True)
    return v


def p25(s, rel, tz, origine):
    for forme in ("utc", "naif"):
        garde_alarme(s)
        e = poser(s, origine, tz, forme)
        dormir(1)
        g = ecart_grossier(s)
        saut = None if g is None else round(g - origine, 1)
        mal_lu = saut is not None and abs(saut) > 30
        d = None if mal_lu else decalage(s, "time", tz)[0]
        r = remettre(s, origine, tz)
        j = {"put_ok": e["put"]["ok"], "saut_s": saut, "saut_h": None if saut is None
             else round(saut / 3600, 2), "mal_lu": mal_lu,
             "decalage_ecart": None if d is None else round(d - origine, 4)}
        rel.ecrire(type="P2-5", forme=forme, ecriture=e, jugement=j, remise=r)
        print(f"P2-5 forme {forme} ({e['texte']}) -> {e['put'].get('status')} "
              f"{e['put'].get('body') if not e['put']['ok'] else ''} saut={j['saut_h']} h "
              f"ecart fin={j['decalage_ecart']} ; remise "
              f"{'conforme' if r['conforme'] else 'NON CONFORME'}", flush=True)


def p23(s, rel, tz, origine, origine_wutim):
    verdicts = []
    for rep in (1, 2, 3):
        garde_alarme(s)
        e = poser(s, origine + DEPLACEMENT, tz)
        dormir(1)
        dt_, est_t = decalage(s, "time", tz)
        dw_, est_w = decalage(s, "wutim", tz)
        j = {"delta_time": None if dt_ is None else round(dt_ - origine, 4),
             "delta_wutim": None if dw_ is None or origine_wutim is None
             else round(dw_ - origine_wutim, 4)}
        j["verdict_time"] = verdict(j["delta_time"], DEPLACEMENT)
        j["verdict_wutim"] = verdict(j["delta_wutim"], DEPLACEMENT)
        r = remettre(s, origine, tz) if rep < 3 else None                    # P2-4
        rel.ecrire(type="P2-3", rep=rep, ecriture=e, estimations_time=est_t,
                   estimations_wutim=est_w, jugement=j, remise=r)
        print(f"P2-3 #{rep} deplacement {DEPLACEMENT} s -> {e['put'].get('status')} "
              f"time {j['delta_time']} ({j['verdict_time']}), wutim {j['delta_wutim']} "
              f"({j['verdict_wutim']})"
              + (f" ; P2-4 remise {'conforme' if r['conforme'] else 'NON CONFORME'} "
                 f"(residuel {r['residuel']} s)" if r else " ; laisse en place pour P2-7"),
              flush=True)
        verdicts.append(j["verdict_time"])
    return verdicts


def p27(s, rel, tz, origine):
    suivi, t0 = [], time.monotonic()
    vise = origine + DEPLACEMENT
    for i in range(1, DUREE_P27 // PAS_P27 + 1):
        attendre_depuis(t0, i * PAS_P27)
        d, est = decalage(s, "time", tz)
        suivi.append({"t": i * PAS_P27, "decalage": d,
                      "ecart_au_deplacement": None if d is None else round(d - vise, 4)})
        print(f"P2-7 +{i * PAS_P27 // 60} min : ecart au deplacement "
              f"{suivi[-1]['ecart_au_deplacement']} s", flush=True)
    bouge = any(x["ecart_au_deplacement"] is not None and abs(x["ecart_au_deplacement"]) > 0.2
                for x in suivi)
    rel.ecrire(type="P2-7", suivi=suivi, bouge_seul=bouge)
    print(f"P2-7 l'horloge a bouge seule : {bouge}", flush=True)


def restaurer(s, rel, origine, tz, time0, wutms0):
    limite = time.monotonic() + DUREE_RESTAURATION / ACCELERATION
    etapes = []
    t = s.corps(0, "time") or {}
    for c in CHAMPS_TIME:
        if c in time0 and not egal_heure(t.get(c), time0[c]):
            etapes.append(s.put(0, "time", {c: time0[c]}))
    w = s.corps(1, "wutms") or {}
    for c in CHAMPS_WUTMS:
        if c in wutms0 and w.get(c) != wutms0[c]:
            etapes.append(s.put(1, "wutms", {c: wutms0[c]}))
    ok, d = False, None
    while time.monotonic() < limite:
        d, _ = decalage(s, "time", tz, interruptible=False)
        if d is not None and abs(d - origine) < TOLERANCE:
            ok = True
            break
        etapes.append(poser(s, origine, tz, interruptible=False))
        dormir(1, interruptible=False)
    t, w = s.corps(0, "time") or {}, s.corps(1, "wutms") or {}
    champs_ok = (all(egal_heure(t.get(c), time0[c]) for c in CHAMPS_TIME if c in time0)
                 and all(w.get(c) == wutms0[c] for c in CHAMPS_WUTMS if c in wutms0))
    rel.ecrire(type="restauration", horloge_conforme=ok, champs_conformes=champs_ok,
               decalage_final=d, origine=origine, etapes=etapes)
    print(f"restauration : horloge {'conforme' if ok else 'NON CONFORME — A VERIFIER'} "
          f"(decalage {None if d is None else round(d, 3)} s, origine {round(origine, 3)} s) ; "
          f"champs {'conformes' if champs_ok else 'NON CONFORMES — A VERIFIER'}", flush=True)
    return ok and champs_ok


def main():
    p = argparse.ArgumentParser(description="P2 — l'ecriture de l'heure du reveil")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--lecture", action="store_true", help="P2-1 seulement, aucune ecriture")
    g.add_argument("--ecriture", action="store_true", help="P2-1 a P2-9")
    p.add_argument("--hote", help="adresse[:port] imposee (faux reveil) ; sinon SSDP")
    a = p.parse_args()
    installer_signaux()
    mode = "lecture" if a.lecture else "ecriture"
    rel = Journal(f"ecriture-heure-{mode}-{time.strftime('%Y%m%dT%H%M%S')}.jsonl")

    def abandon(raison):
        rel.ecrire(type="fin", abandon=raison)
        print(f"ABANDON : {raison}", file=sys.stderr, flush=True)
        return 1

    if not a.hote:
        ntp = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                             capture_output=True, text=True).stdout.strip()
        if ntp != "yes":
            return abandon(f"horloge de la carte non synchronisee (NTPSynchronized={ntp!r})")
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
    dt0, _ = heure_appareil(s)
    time0, wutms0 = s.corps(0, "time"), s.corps(1, "wutms")
    if dt0 is None or time0 is None or wutms0 is None:
        return abandon("time ou wutms illisible")
    tz = dt0.tzinfo
    snap0 = instantane(s)

    mesures = []
    for _ in range(MESURES_P21):                                          # P2-1
        d, est = decalage(s, "time", tz)
        mesures.append({"decalage": d, "estimations": est})
        dormir(5)
    vals = [m["decalage"] for m in mesures if m["decalage"] is not None]
    dispersion = round(max(vals) - min(vals), 4) if len(vals) >= 3 else None
    origine = statistics.median(vals) if vals else None
    origine_wutim, est_w = decalage(s, "wutim", tz)
    rel.ecrire(type="P2-1", time0=time0, wutms0=wutms0, mesures=mesures, origine=origine,
               dispersion=dispersion, origine_wutim=origine_wutim, estimations_wutim=est_w)
    print(f"P2-1 decalage du reveil : {None if origine is None else round(origine, 3)} s, "
          f"dispersion {dispersion} s sur {len(vals)} mesures ; wutim "
          f"{None if origine_wutim is None else round(origine_wutim, 3)} s", flush=True)
    if a.lecture:
        rel.ecrire(type="fin")
        s.fermer()
        print(f"lecture seule terminee -> {rel.nom}", flush=True)
        return 0
    if dispersion is None or dispersion > BRUIT_MAX:
        return abandon(f"dispersion {dispersion} s : la mesure ne peut pas voir 0,5 s")

    bilan = {}
    try:
        bilan["P2-2"] = p22(s, rel, tz, time0, wutms0)
        p26(s, rel, time0)
        effets = effets_de_bord(snap0, instantane(s), ECRITS)
        if effets:
            rel.ecrire(type="P2-9", etape="apres P2-2 et P2-6", effets=effets)
            raise Inattendu(f"effet de bord apres P2-2 et P2-6 : {effets}")
        bilan["P2-8"] = p28(s, rel, tz, origine)
        p25(s, rel, tz, origine)
        bilan["P2-3"] = p23(s, rel, tz, origine, origine_wutim)
        p27(s, rel, tz, origine)
    except Inattendu as exc:
        rel.ecrire(type="arret_securite", raison=str(exc))
        print(f"ARRET DE SECURITE : {exc} — plus aucune ecriture, restauration", flush=True)
    except Arret:
        rel.ecrire(type="interruption")
        print("interrompue — restauration", flush=True)
    finally:
        lever_arret()
        ok = restaurer(s, rel, origine, tz, time0, wutms0)
        snap1 = instantane(s, interruptible=False)
        effets = effets_de_bord(snap0, snap1, ECRITS)
        rel.ecrire(type="P2-9", effets=effets)
        print(f"P2-9 effets hors time/wutms sur toute la sonde : {effets or 'aucun'}", flush=True)
        rel.ecrire(type="fin", bilan=bilan, restaure=ok)
        s.fermer()
    print(f"-> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
