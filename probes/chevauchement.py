"""Le motif reel de l'integration Home Assistant : une requete isolee pendant un
rafraichissement en cours.

Le coordinateur de `theneweinstein/somneo` teste `state_lock.locked()` dans son chemin de
lecture sans jamais acquerir le verrou ; seules les ecritures le prennent. Une action
utilisateur arrivant APRES le debut d'un rafraichissement trouve donc le verrou libre et part
aussitot — deux requetes en vol.

Ce test mesure ce motif precis, et non deux flux paralleles soutenus : un fil enchaine des
lectures comme le fait `fetch_data`, un second envoie UNE requete a un instant tire au hasard
dans cette fenetre. On compte les echecs des deux cotes.

Lecture seule : que des GET.
"""
import http.client
import json
import random
import ssl
import sys
import threading
import time

from somneo_probe import discover

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

# Les ports que `fetch_data` lit en rafraichissement complet.
SEQUENCE = ["wusrd", "wusts", "wulgt", "wudsk", "wualm", "wualm/aenvs", "wualm/aalms"]
ESSAIS = 20


def _get(conn, port):
    try:
        conn.request("GET", f"/di/v1/products/1/{port}",
                     headers={"Connection": "keep-alive"})
        r = conn.getresponse()
        r.read()
        return {"ok": r.status == 200, "port": port}
    except Exception as exc:
        return {"ok": False, "port": port, "error": type(exc).__name__}


def essai(ip):
    """Un rafraichissement complet, avec une requete isolee tiree au hasard dedans."""
    refresh, action = [], []

    def rafraichir():
        conn = http.client.HTTPSConnection(ip, timeout=20, context=CTX)
        for port in SEQUENCE:
            r = _get(conn, port)
            refresh.append(r)
            if not r["ok"]:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = http.client.HTTPSConnection(ip, timeout=20, context=CTX)
        conn.close()

    def agir(delai):
        time.sleep(delai)
        conn = http.client.HTTPSConnection(ip, timeout=20, context=CTX)
        action.append(_get(conn, "wusts"))
        conn.close()

    # La sequence dure ~7 x 0,5 s : on tire l'instant de l'action dedans.
    delai = random.uniform(0.3, 2.5)
    fils = [threading.Thread(target=rafraichir), threading.Thread(target=agir, args=(delai,))]
    for f in fils:
        f.start()
    for f in fils:
        f.join()
    return {"delai": round(delai, 2), "refresh": refresh, "action": action}


def temoin(ip):
    """Meme rafraichissement, sans requete concurrente. Sert de reference."""
    refresh = []
    conn = http.client.HTTPSConnection(ip, timeout=20, context=CTX)
    for port in SEQUENCE:
        refresh.append(_get(conn, port))
    conn.close()
    return refresh


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1

    print(f"Temoin : {ESSAIS // 2} rafraichissements seuls, sans concurrence", flush=True)
    ko_temoin = tot_temoin = 0
    for _ in range(ESSAIS // 2):
        r = temoin(ip)
        tot_temoin += len(r)
        ko_temoin += sum(1 for x in r if not x["ok"])
        time.sleep(2)
    print(f"  -> {tot_temoin - ko_temoin}/{tot_temoin} lectures reussies\n", flush=True)

    print(f"Motif HA : {ESSAIS} rafraichissements, chacun avec UNE action concurrente",
          flush=True)
    essais = []
    for i in range(ESSAIS):
        e = essai(ip)
        essais.append(e)
        ko_r = sum(1 for x in e["refresh"] if not x["ok"])
        ko_a = sum(1 for x in e["action"] if not x["ok"])
        print(f"  essai {i+1:>2} (action a +{e['delai']}s) : "
              f"rafraichissement {len(e['refresh']) - ko_r}/{len(e['refresh'])}, "
              f"action {'ECHEC' if ko_a else 'ok'}", flush=True)
        time.sleep(2)

    tot_r = sum(len(e["refresh"]) for e in essais)
    ko_r = sum(1 for e in essais for x in e["refresh"] if not x["ok"])
    ko_a = sum(1 for e in essais for x in e["action"] if not x["ok"])
    print(f"\nRafraichissement : {tot_r - ko_r}/{tot_r} lectures reussies")
    print(f"Action isolee    : {ESSAIS - ko_a}/{ESSAIS} reussies")
    print(f"Essais avec au moins un echec : "
          f"{sum(1 for e in essais if any(not x['ok'] for x in e['refresh'] + e['action']))}"
          f"/{ESSAIS}")

    nom = f"chevauchement-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump({"temoin": {"total": tot_temoin, "echecs": ko_temoin},
                   "essais": essais}, fh, ensure_ascii=False, indent=2)
    print(f"Releve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
