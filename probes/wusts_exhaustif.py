"""Tout ce qui peut encore contredire le modele en bits de `wusts` — passe finale.

Ecrite le 2026-09-09, apres que la mesure du 06 se soit revelee fausse sur un point (la
veilleuse ne vaut pas 258). Objectif : ne laisser aucune affirmation de la PR `pysomneo`
reposer sur une observation unique ou sur une inference.

Sept volets, du moins intrusif au plus :

  1. INVARIANCE DU NIVEAU — la lampe a 1, 3, 12 et 25 donne-t-elle toujours `257` ? Si le
     niveau entrait dans `wusts`, une table de valeurs serait sans espoir et l'argument change.
  2. DUREE DU TRANSITOIRE `2` — echantillonnee a 0,5 s au lieu de 1,5 s, pour la borner
     vraiment au lieu de dire « environ 6 s ».
  3. VEILLEUSE DEPUIS LE TRANSITOIRE — la lampe passe de 257 a 258 selon le contexte. La
     veilleuse suit-elle la meme regle ?
  4. AFFICHEUR (`dspon`) — le bit 1 est annote « menu utilisateur » dans le code de l'app,
     annotation jamais verifiee. L'afficheur toujours allume le leve-t-il ? Repond a une
     question ouverte depuis le 31 aout.
  5. COUCHER DE SOLEIL AVEC SON — LE TEST DECISIF. `776` (dans la table de `pysomneo`) vaut
     `264` + bit 9. Si activer le son fait passer de 264 a 776, alors `264` n'est pas une
     bizarrerie de cet exemplaire : c'est le meme etat, sans le son. Fait a `sndlv=1`.
  6. LE MEME, DEPUIS LE TRANSITOIRE — la table amont contient `776` ET `777` (= 776 + bit 0).
     Notre coucher de soleil silencieux efface le bit de contexte (264 dans les deux
     contextes). Si l'un des deux contextes rend `777`, la table amont s'explique en entier.
  7. RELAXBREATHING (`wurlx`) — quatrieme usage ordinaire du reveil, jamais mesure. Lumiere et
     son ensemble : quelle valeur, et est-elle dans la table ?

ECRITURE. Touche `wulgt`, `wudsk`, `wusts` (champs d'afficheur) et `wurlx`. **Jamais `wualm`,
jamais `fac`.** Les volets 5 a 7 emettent du son, a volume 1 et quelques secondes : accord
explicite de Victor le 2026-09-09. Tout est relu et restaure en fin de course, y compris les
reglages sonores et l'afficheur.
"""
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

from somneo_probe import discover, get

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PAUSE = 0.2
REPET = 3


def put(ip, port, payload, produit=1):
    url = f"https://{ip}/di/v1/products/{produit}/{port}"
    rec = {"ok": False}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            rec.update(status=r.status, ok=True)
    except urllib.error.HTTPError as exc:
        rec.update(status=exc.code, error=f"HTTP {exc.code}")
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    time.sleep(PAUSE)
    return rec


def lire(ip, port):
    b = (get(ip, 1, port).get("body") or {})
    time.sleep(PAUSE)
    return b


def etat(ip):
    b = lire(ip, "wusts")
    v = b.get("wusts")
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])


def bits_de(v):
    return None if v is None else [i for i in range(16) if v >> i & 1]


def repos(ip, ltlvl, attente=12):
    """Éteint tout et attend la fin du transitoire, pour repartir d'un état propre."""
    put(ip, "wurlx", {"onoff": False})
    put(ip, "wudsk", {"onoff": False})
    put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
    time.sleep(attente)


def main():
    ip = discover()
    if not ip:
        print("Somneo introuvable en SSDP.", file=sys.stderr)
        return 1
    print(f"Somneo : {ip}\n")

    # État initial complet — c'est lui qu'on remettra, champ par champ.
    lgt0 = lire(ip, "wulgt")
    dsk0 = lire(ip, "wudsk")
    sts0 = lire(ip, "wusts")
    rlx0 = lire(ip, "wurlx")
    ltlvl = lgt0.get("ltlvl", 15)
    v0 = sts0.get("wusts")
    print(f"Etat initial : wusts={v0}  lampe={lgt0.get('onoff')} veilleuse={lgt0.get('ngtlt')} "
          f"ltlvl={ltlvl}\n  dspon={sts0.get('dspon')} brght={sts0.get('brght')}  "
          f"dusk snddv={dsk0.get('snddv')} sndlv={dsk0.get('sndlv')} sndch={dsk0.get('sndch')!r}\n")

    res = {"debut": time.time(),
           "initial": {"wusts": v0, "wulgt": lgt0, "wudsk": dsk0, "wusts_champs": sts0,
                       "wurlx": rlx0},
           "volets": {}}

    try:
        # 1 — le niveau de la lampe entre-t-il dans wusts ?
        print("--- 1. invariance du niveau de lampe " + "-" * 20)
        v1 = []
        for niveau in (1, 3, 12, 25):
            repos(ip, ltlvl)
            put(ip, "wulgt", {"onoff": True, "ltlvl": niveau})
            time.sleep(1.2)
            v, b = etat(ip)
            lu = lire(ip, "wulgt")
            print(f"   ltlvl={niveau:<3} -> wusts={str(v):<5} bits={b}  (relu ltlvl={lu.get('ltlvl')})")
            v1.append({"ltlvl": niveau, "wusts": v, "bits": b, "ltlvl_relu": lu.get("ltlvl")})
        vus = {e["wusts"] for e in v1}
        print(f"   => {'INVARIANT' if len(vus) == 1 else 'VARIABLE ' + str(vus)}\n")
        res["volets"]["1_niveau"] = v1

        # 2 — durée du transitoire `2`, échantillonnée fin
        print("--- 2. duree du transitoire `2` " + "-" * 25)
        v2 = []
        for tour in range(REPET):
            repos(ip, ltlvl)
            put(ip, "wulgt", {"onoff": True, "ltlvl": 3})
            time.sleep(1.5)
            put(ip, "wulgt", {"onoff": False})
            t0 = time.monotonic()
            trace = []
            while time.monotonic() - t0 < 20:
                v, _ = etat(ip)
                trace.append({"t": round(time.monotonic() - t0, 2), "wusts": v})
                if v == 1:
                    break
                time.sleep(0.5)
            fin2 = [e["t"] for e in trace if e["wusts"] == 2]
            duree = (max(fin2) - min(fin2)) if fin2 else 0
            print(f"   tour {tour+1} : {[e['wusts'] for e in trace]}  "
                  f"2 vu de t+{min(fin2) if fin2 else 0:.1f}s a t+{max(fin2) if fin2 else 0:.1f}s")
            v2.append({"tour": tour + 1, "trace": trace, "duree_2_s": round(duree, 2)})
        res["volets"]["2_transitoire"] = v2
        print()

        # 3 — la veilleuse suit-elle la règle du bit de contexte ?
        print("--- 3. veilleuse selon le contexte " + "-" * 22)
        v3 = []
        for libelle, delai in (("depuis le repos", 12), ("dans le transitoire", 2)):
            for tour in range(REPET):
                repos(ip, ltlvl, attente=1)
                put(ip, "wulgt", {"onoff": True, "ltlvl": 3})
                time.sleep(1.2)
                put(ip, "wulgt", {"onoff": False})
                time.sleep(delai)
                depart, _ = etat(ip)
                put(ip, "wulgt", {"ngtlt": True})
                time.sleep(1.2)
                v, b = etat(ip)
                lu = lire(ip, "wulgt")
                v3.append({"contexte": libelle, "depart": depart, "wusts": v, "bits": b,
                           "ngtlt_relu": lu.get("ngtlt")})
                print(f"   {libelle:<21} depart={str(depart):<4} -> wusts={str(v):<5} "
                      f"bits={b} (ngtlt={lu.get('ngtlt')})")
                put(ip, "wulgt", {"ngtlt": False})
        res["volets"]["3_veilleuse"] = v3
        print()

        # 4 — l'afficheur toujours allumé lève-t-il le bit 1 ?
        print("--- 4. afficheur (dspon) " + "-" * 32)
        v4 = []
        for etat_dspon in (True, False):
            repos(ip, ltlvl)
            put(ip, "wusts", {"dspon": etat_dspon,
                              "brght": sts0.get("brght", 1)})
            time.sleep(2)
            v, b = etat(ip)
            lu = lire(ip, "wusts")
            print(f"   dspon={str(etat_dspon):<5} -> wusts={str(v):<5} bits={b}  "
                  f"(relu dspon={lu.get('dspon')} brght={lu.get('brght')})")
            v4.append({"dspon": etat_dspon, "wusts": v, "bits": b,
                       "dspon_relu": lu.get("dspon")})
        put(ip, "wusts", {"dspon": bool(sts0.get("dspon")), "brght": sts0.get("brght", 1)})
        res["volets"]["4_afficheur"] = v4
        print()

        # 5 et 6 — LE test : le coucher de soleil avec son, dans les deux contextes.
        print("--- 5/6. coucher de soleil AVEC SON (volume 1) " + "-" * 10)
        v5 = []
        for libelle, delai in (("depuis le repos", 12), ("dans le transitoire", 2)):
            for tour in range(REPET):
                repos(ip, ltlvl, attente=1)
                put(ip, "wulgt", {"onoff": True, "ltlvl": 3})
                time.sleep(1.2)
                put(ip, "wulgt", {"onoff": False})
                time.sleep(delai)
                depart, _ = etat(ip)
                put(ip, "wudsk", {"onoff": True, "snddv": "dus", "sndch": "1", "sndlv": 1})
                time.sleep(2.5)
                v, b = etat(ip)
                lu = lire(ip, "wudsk")
                put(ip, "wudsk", {"onoff": False})
                v5.append({"contexte": libelle, "depart": depart, "wusts": v, "bits": b,
                           "snddv_relu": lu.get("snddv"), "sndlv_relu": lu.get("sndlv")})
                print(f"   {libelle:<21} depart={str(depart):<4} -> wusts={str(v):<5} "
                      f"bits={b}  (snddv={lu.get('snddv')!r} sndlv={lu.get('sndlv')})")
                time.sleep(1)
        res["volets"]["5_sunset_son"] = v5
        print()

        # 7 — RelaxBreathing : lumière et son, quatrième usage ordinaire
        print("--- 7. RelaxBreathing " + "-" * 35)
        v7 = []
        for tour in range(2):
            repos(ip, ltlvl)
            put(ip, "wurlx", {"onoff": True, "sndlv": 1})
            time.sleep(3)
            v, b = etat(ip)
            lu = lire(ip, "wurlx")
            print(f"   tour {tour+1} -> wusts={str(v):<5} bits={b}  (onoff={lu.get('onoff')})")
            v7.append({"tour": tour + 1, "wusts": v, "bits": b, "onoff_relu": lu.get("onoff")})
            put(ip, "wurlx", {"onoff": False})
            time.sleep(2)
        res["volets"]["7_relaxbreathing"] = v7
        print()

    finally:
        print("--- restauration " + "-" * 40)
        put(ip, "wurlx", {"onoff": bool(rlx0.get("onoff")), "sndlv": rlx0.get("sndlv", 8)})
        put(ip, "wudsk", {"onoff": bool(dsk0.get("onoff")),
                          "snddv": dsk0.get("snddv", "off"),
                          "sndch": dsk0.get("sndch", ""),
                          "sndlv": dsk0.get("sndlv", 12)})
        put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")),
                          "ngtlt": bool(lgt0.get("ngtlt")), "ltlvl": ltlvl})
        put(ip, "wusts", {"dspon": bool(sts0.get("dspon")), "brght": sts0.get("brght", 1)})
        time.sleep(10)

        lgt1, dsk1, sts1, rlx1 = (lire(ip, "wulgt"), lire(ip, "wudsk"),
                                  lire(ip, "wusts"), lire(ip, "wurlx"))
        ecarts = {}
        for nom, avant, apres, champs in (
                ("wulgt", lgt0, lgt1, ("onoff", "ngtlt", "ltlvl")),
                ("wudsk", dsk0, dsk1, ("onoff", "snddv", "sndch", "sndlv")),
                ("wusts", sts0, sts1, ("dspon", "brght")),
                ("wurlx", rlx0, rlx1, ("onoff", "sndlv"))):
            for c in champs:
                if avant.get(c) != apres.get(c):
                    ecarts[f"{nom}.{c}"] = {"avant": avant.get(c), "apres": apres.get(c)}
        print(f"   wusts={sts1.get('wusts')} (initial {v0})")
        if ecarts:
            print(f"   ECARTS A CORRIGER A LA MAIN : {json.dumps(ecarts, ensure_ascii=False)}")
        else:
            print("   tout est revenu a l'etat initial")
        res["restauration"] = {"wusts": sts1.get("wusts"), "ecarts": ecarts,
                               "conforme": not ecarts}

        nom = f"wusts-exhaustif-{time.strftime('%Y%m%dT%H%M%S')}.json"
        with open(nom, "w", encoding="utf-8") as fh:
            json.dump(res, fh, ensure_ascii=False, indent=2)
        print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
