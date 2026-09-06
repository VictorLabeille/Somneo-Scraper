"""Trace complete du plantage de `pysomneo.fetch_data()` sur un HF3671/01 reel.

`AttributeError: 'int' object has no attribute 'lower'` sur la methode principale de la
bibliotheque — celle que l'integration Home Assistant appelle en boucle. On veut la ligne
exacte, la valeur fautive et le port d'ou elle vient. Lecture seule.
"""
import sys
import traceback

sys.path.insert(0, ".")
from somneo_probe import discover, get
from pysomneo import Somneo

ip = discover()
print(f"appareil trouve\n")

s = Somneo(ip)
try:
    s.fetch_data(force_slow_refresh=True)
    print("aucun plantage — inattendu")
except Exception:
    print("=== TRACE COMPLETE ===")
    traceback.print_exc()

print("\n=== donnees brutes des ports impliques ===")
for port in ("files/lightthemes", "files/dusklightthemes", "files/wakeup",
             "files/winddowndusk", "wuply", "wudsk"):
    r = get(ip, 1, port, timeout=10)
    corps = r.get("body") if r["ok"] else (r.get("error"), r.get("status"))
    print(f"  {port:<24} {str(corps)[:180]}")

print("\n=== identifiants d'alarme attendus par la bibliotheque ===")
try:
    s2 = Somneo(ip)
    s2._fetch_alarm_data()
    print("  enabled_alarms :", s2.enabled_alarms)
    print("  time_alarms    :", str(s2.time_alarms)[:200])
except Exception as exc:
    print(f"  {type(exc).__name__}: {exc}")
