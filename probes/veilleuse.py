"""Ce que vaut `wusts` quand la veilleuse est allumee — seule, puis par-dessus la lampe.

Sonde d'arbitrage, ecrite le 2026-09-09 apres une contradiction de mesure.

`etats_et_udp.py` avait releve **258** pour « veilleuse allumee » le 2026-09-06, chiffre repris
dans `docs/somneo-api.md` et destine a fonder une PR sur `pysomneo`. `etats_wusts.py` a rejoue
l'etat le 2026-09-09 et obtient **257**, trois fois sur trois — la meme valeur que la lampe.

La difference tient probablement au protocole : la sonde du 06 enchainait lampe, veilleuse et
coucher de soleil **sans restaurer entre eux**, donc la lampe etait encore allumee au moment du
`ngtlt: true`. Celle du 09 remet l'appareil au repos entre chaque etat.

Trois choses a etablir, et aucune ne se deduit — il faut relire `wulgt` apres chaque ecriture,
ce qu'aucune des deux sondes precedentes ne faisait :

1. `ngtlt: true` allume-t-il reellement la veilleuse quand la lampe est eteinte ? (Si `onoff`
   reste `false`, on aura mesure une ecriture sans effet, et `257` viendra d'ailleurs.)
2. Que vaut `wusts` pour la veilleuse **seule** ?
3. Que vaut `wusts` pour veilleuse **par-dessus la lampe allumee** — le contexte du 06 ?

Tant que ce n'est pas tranche, ne rien publier en amont sur la veilleuse : le coucher de soleil
(264, reproduit 3/3) suffit a demontrer le defaut de la table.

ECRITURE. Ecrit sur `wulgt` uniquement, jamais `wualm` ni `fac`. Restaure l'etat initial.
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


def put(ip, port, payload, produit=1):
    url = f"https://{ip}/di/v1/products/{produit}/{port}"
    rec = {"port": port, "payload": payload, "ok": False}
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


def etat(ip):
    """L'état complet qui nous intéresse : ce que dit `wulgt`, et ce que vaut `wusts`."""
    lgt = (get(ip, 1, "wulgt").get("body") or {})
    time.sleep(PAUSE)
    sts = (get(ip, 1, "wusts").get("body") or {})
    time.sleep(PAUSE)
    v = sts.get("wusts")
    return {
        "wusts": v,
        "bits": None if v is None else [i for i in range(16) if v >> i & 1],
        "onoff": lgt.get("onoff"),
        "ngtlt": lgt.get("ngtlt"),
        "ltlvl": lgt.get("ltlvl"),
        "tempy": lgt.get("tempy"),
    }


def montrer(libelle, e):
    print(f"   {libelle:<38} wusts={str(e['wusts']):<6} bits={str(e['bits']):<10} "
          f"onoff={str(e['onoff']):<5} ngtlt={str(e['ngtlt']):<5} ltlvl={e['ltlvl']}")


def main():
    ip = discover()
    if not ip:
        print("Somneo introuvable en SSDP.", file=sys.stderr)
        return 1
    print(f"Somneo : {ip}\n")

    initial = etat(ip)
    montrer("etat initial", initial)
    resultats = {"debut": time.time(), "initial": initial, "sequences": []}

    # Chaque séquence part du repos et n'écrit qu'une chose à la fois, en relisant `wulgt`
    # après chaque écriture : c'est ce qui manquait aux deux sondes précédentes.
    sequences = [
        ("A. veilleuse seule",
         [("ngtlt=True", "wulgt", {"ngtlt": True})]),
        ("B. lampe seule (niveau 3)",
         [("onoff=True ltlvl=3", "wulgt", {"onoff": True, "ltlvl": 3})]),
        ("C. veilleuse PAR-DESSUS la lampe — le contexte du 2026-09-06",
         [("onoff=True ltlvl=3", "wulgt", {"onoff": True, "ltlvl": 3}),
          ("ngtlt=True", "wulgt", {"ngtlt": True})]),
        ("D. veilleuse avec onoff explicite",
         [("onoff=True ngtlt=True", "wulgt", {"onoff": True, "ngtlt": True})]),
    ]

    try:
        for titre, etapes in sequences:
            print(f"\n--- {titre} " + "-" * max(0, 46 - len(titre)))
            trace = []
            for libelle, port, charge in etapes:
                ecriture = put(ip, port, charge)
                time.sleep(1.5)
                e = etat(ip)
                e["ecriture"] = libelle
                e["ecriture_ok"] = ecriture["ok"]
                montrer(libelle, e)
                trace.append(e)

            # Retour au repos, en remettant les trois champs d'un coup.
            put(ip, "wulgt", {"onoff": False, "ngtlt": False,
                              "ltlvl": initial.get("ltlvl", 15)})
            time.sleep(2.5)
            repos = etat(ip)
            montrer("-> retour au repos", repos)
            trace.append(dict(repos, ecriture="extinction"))
            resultats["sequences"].append({"titre": titre, "trace": trace})
            time.sleep(2)
    finally:
        put(ip, "wulgt", {"onoff": bool(initial.get("onoff")),
                          "ngtlt": bool(initial.get("ngtlt")),
                          "ltlvl": initial.get("ltlvl", 15)})
        time.sleep(2.5)
        final = etat(ip)
        conforme = (final["onoff"] == initial["onoff"]
                    and final["ngtlt"] == initial["ngtlt"]
                    and final["ltlvl"] == initial["ltlvl"])
        print("\n--- restauration " + "-" * 30)
        montrer("etat final", final)
        print(f"   conforme a l'initial : {'OUI' if conforme else 'NON — A VERIFIER'}")
        resultats["restauration"] = {"etat": final, "conforme": conforme}

        nom = f"veilleuse-{time.strftime('%Y%m%dT%H%M%S')}.json"
        with open(nom, "w", encoding="utf-8") as fh:
            json.dump(resultats, fh, ensure_ascii=False, indent=2)
        print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
