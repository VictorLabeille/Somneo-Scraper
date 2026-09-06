"""Reproduire le plantage intermittent de `fetch_data()` et en capturer la trace exacte.

Vu une fois sur deux lors de la premiere exploration, jamais reproduit ensuite. On boucle en
alternant les deux formes d'appel, et on affiche la trace complete des la premiere occurrence.
Lecture seule.
"""
import sys
import time
import traceback

sys.path.insert(0, ".")
from somneo_probe import discover
from pysomneo import Somneo

ip = discover()
echecs = 0

for i in range(1, 13):
    # instance neuve une fois sur deux : l'etat interne peut compter
    s = Somneo(ip) if i % 2 else s
    complet = bool(i % 3)
    try:
        if i % 4 == 1:
            s.get_device_info()          # present dans l'execution qui a plante
        s.fetch_data(force_slow_refresh=complet)
        print(f"  {i:>2}. ok      (complet={complet})", flush=True)
    except Exception as exc:
        echecs += 1
        print(f"  {i:>2}. ECHEC   (complet={complet})  {type(exc).__name__}: {exc}", flush=True)
        print("\n=== TRACE ===")
        traceback.print_exc()
        print("=== FIN TRACE ===\n", flush=True)
        if echecs == 1:
            # etat interne au moment du plantage, sans secrets
            for attr in ("sunset_data", "player", "snoozetime", "light_data"):
                print(f"  {attr} = {getattr(s, attr, None)}")
            print(f"  themes dusk_light = {s._dusk_light_themes}")
            print(f"  themes dusk_sound = {s._dusk_sound_themes}")
    time.sleep(1.5)

print(f"\n{echecs}/12 appels ont plante")
