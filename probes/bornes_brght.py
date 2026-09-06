"""Bornes reelles de `brght` — la seule ECRITURE de la campagne. Issue #13 de pysomneo.

`pysomneo.set_display()` documente `brightness: 0-255`. L'application constructeur, elle,
borne a 1-6 (`isWithinLimit()`). L'un des deux ment, et un appelant qui suit la docstring
enverrait 128 a l'appareil. Ce test etablit ce que l'appareil accepte reellement.

PRECAUTIONS, parce que c'est un reveil en service dans une chambre :
  - l'etat courant est lu AVANT et restaure dans un `finally`, meme en cas d'erreur ;
  - la restauration est verifiee par relecture ;
  - `dspon` n'est jamais touche : on ne rallume ni n'eteint l'afficheur ;
  - une seule ecriture a la fois, espacee — l'appareil ne sert qu'une connexion.
"""
import json
import ssl
import sys
import time
import urllib.request

from somneo_probe import discover, get

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

CANDIDATES = [1, 6, 0, 7, 128]   # deux bornes supposees, puis au-dela des deux cotes
ESPACEMENT = 0.4


def put(ip, port, payload):
    """PUT sur un port. Renvoie toujours un dict, ne leve jamais."""
    url = f"https://{ip}/di/v1/products/1/{port}"
    data = json.dumps(payload).encode()
    rec = {"payload": payload, "ok": False}
    try:
        req = urllib.request.Request(url, data=data, method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as resp:
            rec["status"] = resp.status
            rec["reponse"] = json.loads(resp.read().decode("utf-8", errors="replace") or "{}")
            rec["ok"] = True
    except urllib.error.HTTPError as exc:
        rec["status"] = exc.code
        rec["error"] = f"HTTP {exc.code}"
        rec["reponse"] = exc.read().decode("utf-8", errors="replace")
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def lire_affichage(ip):
    r = get(ip, 1, "wusts")
    b = r.get("body") or {}
    return b.get("dspon"), b.get("brght"), r


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1

    dspon0, brght0, _ = lire_affichage(ip)
    print(f"Etat initial : dspon={dspon0}  brght={brght0}")
    if dspon0 is False:
        print("  (afficheur eteint : le changement peut n'etre pas visible)")
    if brght0 is None:
        print("ETAT INITIAL ILLISIBLE — on n'ecrit rien.", file=sys.stderr)
        return 1
    print()

    resultats = []
    try:
        for v in CANDIDATES:
            time.sleep(ESPACEMENT)
            ecriture = put(ip, "wusts", {"brght": v})
            time.sleep(ESPACEMENT)
            _, relu, _ = lire_affichage(ip)
            accepte = relu == v
            resultats.append({"demande": v, "relu": relu, "accepte": accepte,
                              "ecriture": ecriture})
            print(f"  brght={v:>3} -> HTTP {ecriture.get('status', ecriture.get('error'))}"
                  f" | relu {relu} | {'ACCEPTE' if accepte else 'refuse ou borne'}")
    finally:
        time.sleep(ESPACEMENT)
        restauration = put(ip, "wusts", {"brght": brght0})
        time.sleep(ESPACEMENT)
        _, relu, _ = lire_affichage(ip)
        etat = "OK" if relu == brght0 else f"ECHEC (relu {relu})"
        print(f"\nRestauration brght={brght0} : {etat}")
        resultats.append({"restauration": brght0, "relu": relu,
                          "ecriture": restauration})

    nom = f"bornes-brght-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump({"initial": {"dspon": dspon0, "brght": brght0}, "essais": resultats},
                  fh, ensure_ascii=False, indent=2)
    print(f"Releve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
