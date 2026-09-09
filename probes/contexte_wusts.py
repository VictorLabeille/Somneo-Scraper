"""Ce qui fait varier `wusts` a etat lumineux identique — et si `264` est stable.

Sonde de consolidation, ecrite le 2026-09-09 pour fermer les deux dernieres incertitudes avant
de proposer un correctif a `pysomneo`. Elle repond a deux questions, et la seconde peut a elle
seule invalider le correctif envisage.

QUESTION 1 — d'ou vient le bit 0 / bit 1.
`veilleuse.py` a obtenu `257` puis `258` pour **la meme ecriture**. La chronologie suggere que
`wusts` herite du bit de contexte de l'etat ou l'appareil se trouve au moment de l'ecriture :
depuis le repos (`1`, bit 0) on obtient bit 0 ; pendant le transitoire d'extinction (`2`, bit 1,
qui dure ~6 s) on obtient bit 1. C'est une **inference tiree de l'ordre des evenements**, pas
une mesure. On la teste ici en choisissant le delai : allumer 2 s apres une extinction (dans le
transitoire) contre 15 s apres (depuis le repos), en repetant.

QUESTION 2 — `264` est-il stable ?
Le correctif envisage ajoute `264` a la table `STATUS` de `pysomneo`. Si le coucher de soleil
lance depuis le transitoire vaut `266` (bits 1,3,8) au lieu de `264` (bits 3,8), alors **ajouter
une valeur ne suffit pas** : la meme action produirait deux entiers, et la table resterait
fausse par construction. Il faut le savoir AVANT d'ecrire la PR, pas apres.

Ce que la sonde n'etablit pas : ce que le bit 1 designe. Elle mesure ce qui le fait apparaitre.

ECRITURE. `wulgt` et `wudsk` uniquement — jamais `wualm`, jamais `fac`, et aucun son. Chaque
etat est eteint immediatement, l'etat initial est relu en fin de course. Allume la lumiere de
la chambre : accord de Victor le 2026-09-09.
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
REPETITIONS = 3
DELAI_COURT = 2      # dans le transitoire `2`, qui dure ~6 s
DELAI_LONG = 15      # bien après, l'appareil est retombé à `1`


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


def wusts(ip):
    b = (get(ip, 1, "wusts").get("body") or {})
    time.sleep(PAUSE)
    v = b.get("wusts")
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])


def eteindre_tout(ip, ltlvl):
    put(ip, "wudsk", {"onoff": False})
    put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})


def essai(ip, ltlvl, action, delai):
    """Éteint, attend `delai`, relève l'état de départ, agit, relève `wusts`."""
    eteindre_tout(ip, ltlvl)
    time.sleep(delai)
    depart, bits_depart = wusts(ip)

    port, charge = action
    put(ip, port, charge)
    time.sleep(1.2)
    valeur, bits = wusts(ip)

    eteindre_tout(ip, ltlvl)
    return {"delai_s": delai, "depart": depart, "bits_depart": bits_depart,
            "wusts": valeur, "bits": bits}


def main():
    ip = discover()
    if not ip:
        print("Somneo introuvable en SSDP.", file=sys.stderr)
        return 1
    print(f"Somneo : {ip}\n")

    lgt0 = (get(ip, 1, "wulgt").get("body") or {})
    time.sleep(PAUSE)
    ltlvl = lgt0.get("ltlvl", 15)
    v0, _ = wusts(ip)
    print(f"Etat initial : wusts={v0} onoff={lgt0.get('onoff')} ngtlt={lgt0.get('ngtlt')}\n")

    resultats = {"debut": time.time(), "initial": {"wusts": v0, "wulgt": lgt0}, "essais": []}

    actions = [
        ("lampe", ("wulgt", {"onoff": True, "ltlvl": 3})),
        ("coucher de soleil", ("wudsk", {"onoff": True})),
    ]

    try:
        for nom, action in actions:
            print(f"--- {nom} " + "-" * (46 - len(nom)))
            for delai in (DELAI_LONG, DELAI_COURT):
                contexte = "depuis le repos" if delai == DELAI_LONG else "dans le transitoire"
                vus = []
                for tour in range(1, REPETITIONS + 1):
                    r = essai(ip, ltlvl, action, delai)
                    r.update(action=nom, contexte=contexte, tour=tour)
                    resultats["essais"].append(r)
                    vus.append(r["wusts"])
                    print(f"   t+{delai:>2}s {contexte:<21} depart={str(r['depart']):<4}"
                          f" -> wusts={str(r['wusts']):<5} bits={r['bits']}")
                accord = "CONSTANT" if len(set(vus)) == 1 else "VARIABLE " + str(vus)
                print(f"      => {accord}\n")
    finally:
        put(ip, "wudsk", {"onoff": bool((get(ip, 1, "wudsk").get("body") or {}).get("onoff"))})
        put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")),
                          "ngtlt": bool(lgt0.get("ngtlt")), "ltlvl": ltlvl})
        time.sleep(3)
        lgt1 = (get(ip, 1, "wulgt").get("body") or {})
        time.sleep(PAUSE)
        v1, _ = wusts(ip)
        conforme = (lgt1.get("onoff") == lgt0.get("onoff")
                    and lgt1.get("ngtlt") == lgt0.get("ngtlt"))
        print(f"--- restauration : wusts={v1} (initial {v0}) onoff={lgt1.get('onoff')} "
              f"ngtlt={lgt1.get('ngtlt')} -> {'OK' if conforme else 'A VERIFIER'}")
        resultats["restauration"] = {"wusts": v1, "wulgt": lgt1, "conforme": conforme}

        nom_fichier = f"contexte-wusts-{time.strftime('%Y%m%dT%H%M%S')}.json"
        with open(nom_fichier, "w", encoding="utf-8") as fh:
            json.dump(resultats, fh, ensure_ascii=False, indent=2)
        print(f"\nReleve -> {nom_fichier}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
