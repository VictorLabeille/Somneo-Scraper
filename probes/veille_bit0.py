"""Qu'est-ce qui leve le bit 0 sur un coucher de soleil : la duree de veille, ou l'heure ?

Le 2026-09-09, le meme geste a donne `264` (bits 3, 8) a 19 h et `265` (bits 0, 3, 8) a 22 h,
six fois chaque. Les deux voies d'ecriture ont ete comparees dans la meme minute et donnent le
meme resultat : ce n'est pas `pysomneo`. Reste a savoir ce qui a change dans l'appareil.

Cela commande directement la PR : si le coucher de soleil peut valoir `264` **ou** `265`,
ajouter `264` a la table `STATUS` ne corrige rien la moitie du temps.

Deux hypotheses, une seule testable ce soir :
  (a) LA DUREE DE VEILLE avant l'action. A 19 h l'appareil etait manipule sans arret ; a 22 h il
      dormait depuis deux heures. On fait donc varier le repos : 10 s, 60 s, 180 s, 300 s.
  (b) l'heure ou la lumiere ambiante — non manipulables, mais on releve la classe de luminosite
      a chaque essai pour pouvoir recouper plus tard.

Si le bit 0 apparait a partir d'un certain repos, l'hypothese (a) tient et la regle est
connue. S'il est present partout ce soir, c'est (b) et il faudra rejouer de jour.

VIE PRIVEE. `mslux` decrit la chambre : le JEU DE DONNEES VERSE ne porte qu'une **classe**
(`sombre`/`clair`), jamais la valeur. Voir AGENTS.md, § Depot public.

ECRITURE. `wudsk` seulement, restaure. Aucun son.
"""
import json, ssl, sys, time, urllib.request
sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
REPOS = [10, 60, 180, 300]
REPET = 2

def put(ip, port, payload):
    req = urllib.request.Request(f"https://{ip}/di/v1/products/1/{port}",
        data=json.dumps(payload).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15, context=CTX).read()
    except Exception as e:
        print("      PUT ->", type(e).__name__, flush=True)
    time.sleep(0.2)

def lire(ip, port="wusts", essais=4):
    for _ in range(essais):
        b = get(ip, 1, port).get("body")
        time.sleep(0.2)
        if b:
            return b
        time.sleep(1.5)
    return {}

def etat(ip):
    v = lire(ip).get("wusts")
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])

def classe_lux(ip):
    """Classe qualitative seulement : la valeur decrit la chambre et ne se publie pas."""
    lux = lire(ip, "wusrd").get("mslux")
    if lux is None:
        return "inconnue"
    return "sombre" if lux < 50 else "clair"

ip = discover(); print("Somneo :", ip, flush=True)
dsk0 = lire(ip, "wudsk")
print(f"debut {time.strftime('%H:%M:%S')}  luminosite : {classe_lux(ip)}\n", flush=True)
res = {"debut": time.time(), "essais": []}

try:
    for attente in REPOS:
        for tour in range(1, REPET + 1):
            put(ip, "wudsk", {"onoff": False})
            put(ip, "wulgt", {"onoff": False, "ngtlt": False})
            time.sleep(attente)
            depart, _ = etat(ip)
            put(ip, "wudsk", {"onoff": True})
            time.sleep(2.5)
            v, b = etat(ip)
            on = lire(ip, "wudsk").get("onoff")
            lux = classe_lux(ip)
            print(f"   repos {attente:>3}s  tour {tour}  depart={depart} -> wusts={v} "
                  f"bits={b} (onoff={on}, {lux})", flush=True)
            res["essais"].append({"repos_s": attente, "tour": tour, "depart": depart,
                                  "wusts": v, "bits": b, "onoff": on, "lux_classe": lux,
                                  "heure": time.strftime("%H:%M:%S")})
            put(ip, "wudsk", {"onoff": False})
            time.sleep(2)
finally:
    put(ip, "wudsk", {"onoff": bool(dsk0.get("onoff"))})
    put(ip, "wulgt", {"onoff": False, "ngtlt": False})
    time.sleep(10)
    v, _ = etat(ip)
    print(f"\n--- restauration : wusts={v}", flush=True)
    res["restauration"] = {"wusts": v}
    nom = f"veille-bit0-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
    print("Releve ->", nom, flush=True)
