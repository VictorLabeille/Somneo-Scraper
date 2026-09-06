"""Que fait `pysomneo` quand l'appareil se derobe ? — le lien entre l'issue #8 et le code.

On sait que l'appareil ne sert qu'une connexion et lache celle qui est en vol. Question :
la bibliotheque echoue-t-elle PROPREMENT (exception reseau explicite) ou plante-t-elle avec
une erreur qui n'a rien a voir, du genre `AttributeError: 'int' object has no attribute
'lower'` — vue une fois lors de l'exploration, jamais reproduite au calme ?

Protocole : un fil de bruit maintient des requetes concurrentes ; le fil principal appelle
`fetch_data()` en boucle et classe ce qui sort. Lecture seule des deux cotes.
"""
import collections
import http.client
import ssl
import sys
import threading
import time
import traceback

sys.path.insert(0, ".")
from somneo_probe import discover
from pysomneo import Somneo

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ARRET = threading.Event()


def bruit(ip):
    """Maintient une seconde requete en vol, en permanence."""
    while not ARRET.is_set():
        try:
            c = http.client.HTTPSConnection(ip, timeout=10, context=CTX)
            c.request("GET", "/di/v1/products/1/wusrd")
            c.getresponse().read()
            c.close()
        except Exception:
            pass


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1

    print("Temoin — 5 appels a fetch_data() sans bruit :")
    s = Somneo(ip)
    for i in range(5):
        try:
            s.fetch_data(force_slow_refresh=True)
            print(f"  {i+1}. ok")
        except Exception as exc:
            print(f"  {i+1}. {type(exc).__name__}: {exc}")
        time.sleep(0.5)

    print("\nSous concurrence — 12 appels, deux fils de bruit :")
    fils = [threading.Thread(target=bruit, args=(ip,), daemon=True) for _ in range(2)]
    for f in fils:
        f.start()
    time.sleep(1)

    classes = collections.Counter()
    premiere_trace = None
    for i in range(12):
        s2 = Somneo(ip)
        try:
            s2.fetch_data(force_slow_refresh=True)
            classes["ok"] += 1
            print(f"  {i+1:>2}. ok", flush=True)
        except Exception as exc:
            nom = type(exc).__name__
            classes[nom] += 1
            print(f"  {i+1:>2}. {nom}: {str(exc)[:90]}", flush=True)
            if premiere_trace is None and nom not in ("ConnectionError", "ReadTimeout",
                                                      "Timeout", "RequestException"):
                premiere_trace = traceback.format_exc()
        time.sleep(0.5)

    ARRET.set()
    time.sleep(1)

    print(f"\nBilan : {dict(classes)}")
    if premiere_trace:
        print("\n=== PREMIERE ERREUR NON RESEAU — trace complete ===")
        print(premiere_trace)
    else:
        print("\nAucune erreur non reseau : la bibliotheque echoue proprement.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
