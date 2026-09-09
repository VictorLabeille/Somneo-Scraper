"""Les valeurs de `wusts` que produisent trois usages ordinaires, mesurees en repetition.

Fonde la correction proposee a `pysomneo` : sa table `STATUS` de `const.py` traite `wusts`
comme un identifiant d'etat, alors que c'est un champ de bits. Deux consequences distinctes,
et cette sonde mesure les deux.

1. LE TROU. Veilleuse et coucher de soleil produisent des valeurs absentes de la table, donc
   `STATUS.get(...)` renvoie `unknown`. La sonde du 2026-09-06 (`etats_et_udp.py`) l'avait
   releve, mais **une seule fois par etat**. Ici chaque etat est provoque N fois, avec retour
   au repos verifie entre deux, pour que le chiffre publie en amont soit reproductible et non
   anecdotique.

2. L'ETIQUETTE FAUSSE. La table dit `2: "sunset"` alors que le coucher de soleil de cet
   appareil vaut 264. `2` a ete vu quatre fois (restauration du 06, alarmes des 07 et 09,
   extinction de lampe du 08) et **toujours juste avant le retour a `1`**. Mais les captures
   echantillonnaient a 30 s : on ignore combien de temps il dure, et une extinction du 08 au
   soir est passee de 257 a 1 sans qu'il apparaisse. La phase 2 echantillonne donc l'extinction
   a haute cadence pour trancher : etat de passage systematique, ou artefact d'echantillonnage.

Ce que la sonde n'etablit pas, et qu'il ne faut pas lui faire dire : ce que `2` *designe*.
Elle mesure quand il apparait et combien il dure, pas ce qu'affiche l'appareil.

ECRITURE. Cette sonde ecrit sur l'appareil — `wulgt` et `wudsk`, jamais `wualm` ni `fac`.
Chaque etat est restaure immediatement, et l'etat initial est relu en fin de course. Elle
allume donc la lumiere de la chambre : ne pas la lancer sans accord, et jamais le soir sans
raison (voir AGENTS.md). Accord de Victor le 2026-09-09.

Usage : deposer sur la Radxa a cote de somneo_probe.py, puis `python3 etats_wusts.py [N]`.
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

PAUSE = 0.2          # sérialisation : ~200 ms entre deux appels (AGENTS.md)
REPETITIONS = 3      # combien de fois chaque état est provoqué
CADENCE_FINE = 1.5   # échantillonnage de l'extinction, en secondes
DUREE_FINE = 90      # combien de temps on suit une extinction


def put(ip, port, payload, produit=1):
    """PUT sur un port. Ne lève jamais : le rec dit si ça a abouti."""
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


def lire_wusts(ip):
    rec = get(ip, 1, "wusts")
    time.sleep(PAUSE)
    body = rec.get("body") or {}
    return body.get("wusts"), body.get("wutim")


def bits(valeur):
    """Les rangs des bits à 1, du poids faible au poids fort."""
    if valeur is None:
        return None
    return [i for i in range(16) if valeur >> i & 1]


def attendre_repos(ip, limite=60):
    """Rend la main quand `wusts` revient à 1, ou au bout de `limite` secondes.

    Renvoie la trace complète : c'est elle qui porte l'observation de `2`.
    """
    trace = []
    debut = time.monotonic()
    while time.monotonic() - debut < limite:
        v, wutim = lire_wusts(ip)
        trace.append({"t": round(time.monotonic() - debut, 2), "wusts": v, "wutim": wutim})
        if v == 1:
            break
        time.sleep(CADENCE_FINE)
    return trace


def main():
    repetitions = int(sys.argv[1]) if len(sys.argv) > 1 else REPETITIONS

    ip = discover()
    if not ip:
        print("Somneo introuvable en SSDP.", file=sys.stderr)
        return 1
    print(f"Somneo : {ip}\n")

    lgt0 = (get(ip, 1, "wulgt").get("body") or {})
    time.sleep(PAUSE)
    dsk0 = (get(ip, 1, "wudsk").get("body") or {})
    time.sleep(PAUSE)
    v0, _ = lire_wusts(ip)
    print(f"Etat initial : wusts={v0}  lumiere onoff={lgt0.get('onoff')} "
          f"ltlvl={lgt0.get('ltlvl')} ngtlt={lgt0.get('ngtlt')}  "
          f"coucher de soleil onoff={dsk0.get('onoff')}\n")

    resultats = {
        "debut": time.time(),
        "initial": {"wusts": v0, "wulgt": lgt0, "wudsk": dsk0},
        "repetitions": repetitions,
        "etats": [],
        "extinctions": [],
    }

    # Un état = un PUT unique, la lecture qui suit, puis le retour au repos.
    etapes = [
        ("lumiere allumee (niveau 3)", "wulgt", {"onoff": True, "ltlvl": 3},
         "wulgt", {"onoff": False}),
        ("veilleuse allumee", "wulgt", {"ngtlt": True},
         "wulgt", {"ngtlt": False}),
        ("coucher de soleil lance", "wudsk", {"onoff": True},
         "wudsk", {"onoff": False}),
    ]

    try:
        for tour in range(1, repetitions + 1):
            print(f"--- tour {tour}/{repetitions} " + "-" * 40)
            for libelle, port, charge, port_off, charge_off in etapes:
                ecriture = put(ip, port, charge)
                if not ecriture["ok"]:
                    print(f"   {libelle:<30} ECHEC : {ecriture.get('error')}")
                    resultats["etats"].append({"tour": tour, "libelle": libelle,
                                               "erreur": ecriture.get("error")})
                    continue

                # Trois lectures rapprochées : la valeur doit être stable, pas un instantané
                # heureux. Si elle bouge, c'est le fait qui compte.
                releves = []
                for _ in range(3):
                    v, wutim = lire_wusts(ip)
                    releves.append(v)
                    time.sleep(0.8)
                v = releves[0]
                stable = len(set(releves)) == 1
                print(f"   {libelle:<30} wusts={v:<6} bits={bits(v)}  "
                      f"{'stable' if stable else 'INSTABLE ' + str(releves)}")
                resultats["etats"].append({"tour": tour, "libelle": libelle, "wusts": v,
                                           "bits": bits(v), "releves": releves,
                                           "stable": stable})

                # Extinction, suivie à haute cadence : c'est là qu'on attrape `2`.
                put(ip, port_off, charge_off)
                trace = attendre_repos(ip)
                vus = [e["wusts"] for e in trace]
                duree_2 = sum(CADENCE_FINE for e in trace if e["wusts"] == 2)
                print(f"      extinction -> {vus}"
                      + (f"   (2 tenu ~{duree_2:.1f}s)" if 2 in vus else "   (pas de 2)"))
                resultats["extinctions"].append({"tour": tour, "apres": libelle,
                                                 "trace": trace, "vu_2": 2 in vus,
                                                 "duree_2_s": duree_2 if 2 in vus else 0})
                time.sleep(2)
    finally:
        # Restauration : on remet exactement ce qui était là au départ.
        print("\n--- restauration " + "-" * 40)
        put(ip, "wudsk", {"onoff": bool(dsk0.get("onoff"))})
        put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")),
                          "ltlvl": lgt0.get("ltlvl", 15),
                          "ngtlt": bool(lgt0.get("ngtlt"))})
        time.sleep(3)
        lgt1 = (get(ip, 1, "wulgt").get("body") or {})
        time.sleep(PAUSE)
        v1, _ = lire_wusts(ip)
        conforme = (lgt1.get("onoff") == lgt0.get("onoff")
                    and lgt1.get("ngtlt") == lgt0.get("ngtlt"))
        print(f"   wusts={v1} (initial {v0})  onoff={lgt1.get('onoff')} "
              f"ngtlt={lgt1.get('ngtlt')} -> {'OK' if conforme else 'A VERIFIER'}")
        resultats["restauration"] = {"wusts": v1, "wulgt": lgt1, "conforme": conforme}

        nom = f"etats-wusts-{time.strftime('%Y%m%dT%H%M%S')}.json"
        with open(nom, "w", encoding="utf-8") as fh:
            json.dump(resultats, fh, ensure_ascii=False, indent=2)
        print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
