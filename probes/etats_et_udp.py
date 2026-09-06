"""Deux experiences en une : l'abonnement UDP, et les etats du champ de bits `wusts`.

Elles s'alimentent. On s'abonne d'abord aux notifications du port de la lumiere, PUIS on
allume — les changements provoques servent a verifier si l'appareil notifie reellement, ce
qui n'a jamais ete teste (§6 de docs/somneo-api.md).

`wusts` est un champ de bits que `pysomneo` traite comme une table de 8 valeurs magiques.
Chaque etat releve ici est une valeur que la table couvre ou ne couvre pas — c'est la matiere
du correctif.

ECRITURES, avec restauration verifiee : lumiere a faible intensite, veilleuse, coucher de
soleil. Jamais d'alarme. L'abonnement a un `ttl` court et expire tout seul.
"""
import json
import socket
import ssl
import sys
import threading
import time
import urllib.request

sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
PORT_UDP = 9999
TTL = 120

recus = []
ARRET = threading.Event()


def put(ip, port, payload, produit=1):
    url = f"https://{ip}/di/v1/products/{produit}/{port}"
    rec = {"payload": payload, "ok": False}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            rec.update(status=r.status, ok=True,
                       reponse=r.read().decode("utf-8", errors="replace")[:200])
    except urllib.error.HTTPError as exc:
        rec.update(status=exc.code, error=f"HTTP {exc.code}",
                   reponse=exc.read().decode("utf-8", errors="replace")[:200])
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def post(ip, port, payload, produit=1):
    url = f"https://{ip}/di/v1/products/{produit}/{port}"
    rec = {"payload": payload, "ok": False}
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "X-Condor-Features": "changeindication-port"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            rec.update(status=r.status, ok=True,
                       reponse=r.read().decode("utf-8", errors="replace")[:300])
    except urllib.error.HTTPError as exc:
        rec.update(status=exc.code, error=f"HTTP {exc.code}",
                   reponse=exc.read().decode("utf-8", errors="replace")[:300])
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def ecouteur():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", PORT_UDP))
    s.settimeout(1.0)
    while not ARRET.is_set():
        try:
            data, addr = s.recvfrom(4096)
            recus.append({"t": time.time(), "octets": len(data),
                          "contenu": data.decode("utf-8", errors="replace")[:300]})
            print(f"    << datagramme UDP : {data[:160]!r}", flush=True)
        except socket.timeout:
            pass
    s.close()


def wusts(ip):
    b = get(ip, 1, "wusts").get("body") or {}
    return b.get("wusts"), b


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1

    lgt0 = (get(ip, 1, "wulgt").get("body") or {})
    dsk0 = (get(ip, 1, "wudsk").get("body") or {})
    v0, _ = wusts(ip)
    print(f"Etat initial : wusts={v0}  lumiere onoff={lgt0.get('onoff')} "
          f"ltlvl={lgt0.get('ltlvl')} ngtlt={lgt0.get('ngtlt')}  dusk onoff={dsk0.get('onoff')}\n")

    resultats = {"initial": {"wusts": v0, "wulgt": lgt0, "wudsk": dsk0}, "etats": [], "udp": {}}

    # --- 1. Abonnement UDP ---
    print("1. Abonnement UDP sur le port de la lumiere")
    threading.Thread(target=ecouteur, daemon=True).start()
    time.sleep(0.5)
    ab = post(ip, "wulgt", {"subscriber": "somneo-scraper-probe", "ttl": TTL,
                            "changeudp": PORT_UDP})
    print(f"   POST wulgt -> {ab.get('status') or ab.get('error')}  {ab.get('reponse','')[:120]}")
    resultats["udp"]["abonnement"] = ab
    time.sleep(0.5)
    sub = get(ip, 0, "sub", timeout=10)
    print(f"   port sub -> {json.dumps(sub.get('body'))[:200] if sub['ok'] else sub.get('error')}")
    resultats["udp"]["sub_apres"] = sub.get("body") if sub["ok"] else sub.get("error")

    # --- 2. Etats de wusts ---
    print("\n2. Etats du champ de bits wusts")
    etapes = [
        ("lumiere allumee (niveau 3)", "wulgt", {"onoff": True, "ltlvl": 3}),
        ("veilleuse allumee",          "wulgt", {"ngtlt": True}),
        ("coucher de soleil lance",    "wudsk", {"onoff": True}),
    ]
    try:
        for libelle, port, payload in etapes:
            put(ip, port, payload)
            time.sleep(1.5)
            v, brut = wusts(ip)
            bits = format(v, "016b") if isinstance(v, int) else "?"
            print(f"   {libelle:<30} wusts={v:<6} bits={bits}")
            resultats["etats"].append({"libelle": libelle, "wusts": v, "bits": bits})
            time.sleep(0.5)
    finally:
        print("\n3. Restauration")
        put(ip, "wudsk", {"onoff": bool(dsk0.get("onoff"))})
        time.sleep(0.5)
        put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")),
                          "ltlvl": int(lgt0.get("ltlvl", 0)),
                          "ngtlt": bool(lgt0.get("ngtlt"))})
        time.sleep(1.5)
        v1, _ = wusts(ip)
        lgt1 = get(ip, 1, "wulgt").get("body") or {}
        ok = (v1 == v0 and lgt1.get("onoff") == lgt0.get("onoff")
              and lgt1.get("ngtlt") == lgt0.get("ngtlt"))
        print(f"   wusts={v1} (initial {v0})  lumiere onoff={lgt1.get('onoff')} "
              f"ngtlt={lgt1.get('ngtlt')}  -> {'OK' if ok else 'A VERIFIER'}")
        resultats["restauration"] = {"wusts": v1, "wulgt": lgt1, "conforme": ok}

    time.sleep(3)
    ARRET.set()
    resultats["udp"]["datagrammes"] = recus
    print(f"\n4. Datagrammes UDP recus : {len(recus)}")
    if not recus:
        print("   Aucun. L'abonnement expire seul dans "
              f"{TTL}s — rien a nettoyer.")

    nom = f"etats-udp-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(resultats, fh, ensure_ascii=False, indent=2)
    print(f"Releve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
