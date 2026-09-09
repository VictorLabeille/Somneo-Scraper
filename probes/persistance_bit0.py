"""Combien de temps l'appareil garde-t-il le bit 0 eteint apres une sollicitation lumineuse ?

Etabli le 2026-09-09 : un coucher de soleil vaut `265` (bits 0, 3, 8) quand l'appareil n'a pas
vu de lumiere depuis longtemps, et `264` (bits 3, 8) sitot qu'une lampe a ete allumee — l'effet
persistant ensuite sans qu'il faille rallumer. C'est la seule explication qui recoupe toute la
soiree : 264 partout a 19 h (campagne qui allumait sans cesse), 265 partout entre 21 h 56 et
22 h 20 (aucune lumiere depuis 1 h 30), bascule a 264 des le premier allumage a 22 h 25.

Reste a borner la persistance, et c'est ce qui decide de la PR : si le bit 0 revient au bout de
quelques minutes, **les deux valeurs se rencontrent en usage courant** et la table doit porter
`264` et `265`. S'il faut des heures, `265` est un cas de reveil rare — a documenter, pas
forcement a corriger.

Protocole : une lampe allumee puis eteinte pour poser l'etat a 264, puis des couchers de soleil
espaces de 2, 5, 10 et 20 minutes, sans plus jamais toucher a la lumiere. On note quand le
bit 0 revient.

Long (~40 min) et sans surveillance : ecrit son releve au fil de l'eau pour ne rien perdre si
la capture reprend la main.

ECRITURE. `wudsk` et `wulgt`, restaures. Aucun son.
"""
import json, ssl, sys, time, urllib.request
sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
PALIERS = [120, 300, 600, 1200]
NOM = f"persistance-bit0-{time.strftime('%Y%m%dT%H%M%S')}.json"

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

ip = discover(); print("Somneo :", ip, flush=True)
lgt0 = lire(ip, "wulgt"); ltlvl = lgt0.get("ltlvl", 15)
res = {"debut": time.time(), "essais": []}

def sauver():
    json.dump(res, open(NOM, "w"), ensure_ascii=False, indent=2)

try:
    # On pose l'état : une lampe allumée puis éteinte doit ramener le coucher de soleil à 264.
    put(ip, "wudsk", {"onoff": False})
    put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(1.5)
    put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl}); time.sleep(15)
    put(ip, "wudsk", {"onoff": True}); time.sleep(2.5)
    v, b = etat(ip)
    print(f"  amorce (lampe puis sunset)      -> wusts={v} bits={b}", flush=True)
    res["essais"].append({"palier_s": 0, "wusts": v, "bits": b,
                          "heure": time.strftime("%H:%M:%S")})
    put(ip, "wudsk", {"onoff": False})
    sauver()

    for attente in PALIERS:
        time.sleep(attente)          # aucune lumière pendant tout ce temps
        depart, _ = etat(ip)
        put(ip, "wudsk", {"onoff": True}); time.sleep(2.5)
        v, b = etat(ip)
        marque = "  <-- le bit 0 est revenu" if v == 265 else ""
        print(f"  apres {attente//60:>2} min sans lumiere  depart={depart} -> "
              f"wusts={v} bits={b}{marque}", flush=True)
        res["essais"].append({"palier_s": attente, "depart": depart, "wusts": v, "bits": b,
                              "heure": time.strftime("%H:%M:%S")})
        put(ip, "wudsk", {"onoff": False})
        sauver()
finally:
    put(ip, "wudsk", {"onoff": False})
    put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")), "ngtlt": bool(lgt0.get("ngtlt")),
                      "ltlvl": ltlvl})
    time.sleep(10)
    v, _ = etat(ip)
    print(f"\n--- restauration : wusts={v}", flush=True)
    res["restauration"] = {"wusts": v}
    sauver()
    print("Releve ->", NOM, flush=True)
