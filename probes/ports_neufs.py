"""Caracterise les trois ports mis au jour par le lot du 7 septembre 2026.

Le balayage exhaustif des noms de trois lettres a fait apparaitre `dsi` et `fac`, et la
surface des sous-ressources a fait apparaitre `dataupload/event.1/data`. Aucun des trois
n'etait connu : ni dans l'APK, ni dans `pysomneo`, ni dans les releves du 31 aout.

Trois questions, une par port :
  1. `dataupload/event.1/data` — memoire ou instantane ? Le seul port qui ait renvoye un
     evenement date (`endalarm`). S'il retient le dernier evenement, le collecteur peut
     dater une alarme sans interroger `wusts` toutes les 30 s. Repondre suppose de le
     relire dans le temps et de voir si `stime` bouge.
  2. `dsi` — que porte `keypress` ? Un port d'afficheur qui exposerait les appuis boutons
     donnerait le geste « je me couche » sans passer par l'application.
  3. `fac` — REINITIALISATION USINE. Lecture seule, jamais d'ecriture : `{"wifi":0,"reset":0}`
     est un declencheur, pas un etat. On ne fait que confirmer sa presence et sa forme.

Aucune ecriture. La sonde est lente a dessein : un appel a la fois, espaces, sur un appareil
qui ne sert qu'une connexion.
"""
import json
import sys
import time

sys.path.insert(0, ".")
from somneo_probe import discover, get

REDACTED = {"serial", "macaddress", "cppid", "ssid", "key", "nextkey", "password"}

# 20 min a raison d'un tour par minute : assez pour voir bouger un evenement s'il bouge,
# assez court pour rendre l'appareil avant la soiree.
TOURS = 20
PAUSE_TOUR = 60
PAUSE_APPEL = 0.2


def sans_secrets(o):
    if isinstance(o, dict):
        return {k: ("<retire>" if k.lower() in REDACTED else sans_secrets(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [sans_secrets(v) for v in o]
    return o


def lire(ip, produit, port):
    time.sleep(PAUSE_APPEL)
    rec = get(ip, produit, port)
    rec["body"] = sans_secrets(rec.get("body"))
    return rec


def volet_forme(ip):
    """Ce que chaque port renvoie, et les sous-ressources voisines qu'on n'a pas essayees."""
    print("=" * 60)
    print("1. Forme des ports neufs")
    print("=" * 60)
    out = {}
    cibles = [
        (1, "dsi"),
        (1, "fac"),
        (1, "dataupload/event.1/data"),
        (1, "dataupload/event.1"),
        # Le suffixe `.1` est une supposition heritee des capteurs : y en a-t-il d'autres ?
        (1, "dataupload/event.2/data"),
        (1, "dataupload/event/data"),
        # `dsi` est peut-etre lui aussi une racine a sous-ressources.
        (1, "dsi/keypress"),
        (1, "dsi/screen"),
    ]
    for produit, port in cibles:
        rec = lire(ip, produit, port)
        etat = rec.get("status", rec.get("error", "?"))
        corps = json.dumps(rec.get("body"), ensure_ascii=False) if rec.get("ok") else ""
        print(f"  {port:<28} {str(etat):<6} {corps[:110]}")
        out[port] = rec
    return out


def volet_evenements(ip):
    """`event.1/data` retient-il quelque chose, et `dsi` voit-il les appuis ?

    On releve les deux ensemble : si un bouton est presse pendant la sonde, on saura
    lequel des deux ports l'a vu.
    """
    print()
    print("=" * 60)
    print(f"2. Suivi sur {TOURS} min — l'evenement bouge-t-il ?")
    print("=" * 60)
    print("  (appuyer sur un bouton du reveil pendant ce temps rend la mesure plus riche)")
    serie = []
    vus = set()
    for tour in range(TOURS):
        horo = time.strftime("%H:%M:%S")
        ev = lire(ip, 1, "dataupload/event.1/data")
        ds = lire(ip, 1, "dsi")
        st = lire(ip, 1, "wusts")
        ligne = {
            "ts": time.time(),
            "event": ev.get("body") if ev.get("ok") else {"erreur": ev.get("error")},
            "dsi": ds.get("body") if ds.get("ok") else {"erreur": ds.get("error")},
            "wusts": (st.get("body") or {}).get("wusts") if st.get("ok") else None,
        }
        serie.append(ligne)

        cle = json.dumps([ligne["event"], ligne["dsi"]], sort_keys=True)
        neuf = "  <<< CHANGEMENT" if vus and cle not in vus else ""
        vus.add(cle)
        e = ligne["event"] if isinstance(ligne["event"], dict) else {}
        print(f"  {horo}  event={e.get('event')!r} stime={e.get('stime')!r} "
              f"pname={e.get('pname')!r} | dsi={json.dumps(ligne['dsi'])} "
              f"| wusts={ligne['wusts']}{neuf}")

        if tour < TOURS - 1:
            time.sleep(PAUSE_TOUR)
    return serie


def main():
    ip = discover()
    if not ip:
        print("Somneo introuvable en SSDP — sonde abandonnee.")
        return 1
    print(f"Somneo decouvert. Sonde en lecture seule, {TOURS} min.\n")

    releve = {"forme": volet_forme(ip), "serie": volet_evenements(ip)}

    nom = time.strftime("ports-neufs-%Y%m%dT%H%M%S.json")
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(releve, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
