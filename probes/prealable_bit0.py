"""Le bit 0 depend-il d'une action lumineuse ANTERIEURE au coucher de soleil ?

Derniere hypothese en lice, et elle vient d'une relecture des protocoles plutot que d'une
intuition. Le 2026-09-09 a 19 h, `contexte_wusts.py` obtenait `264` — mais chacun de ses essais
**allumait puis eteignait la lampe** avant de lancer le coucher de soleil, pour fabriquer le
contexte de depart. A 22 h, `veille.py` lance le coucher de soleil sans rien avant, et obtient
`265`, quelle que soit la duree de repos (10 s comme 60 s). La duree de veille est donc hors de
cause, l'heure et la luminosite aussi (classee « clair » dans les deux cas).

Reste la seule difference de protocole : une action lumineuse recente.

  A. repos, puis coucher de soleil directement          -> attendu 265 si l'hypothese tient
  B. repos, lampe allumee puis eteinte, puis coucher    -> attendu 264

Les deux sont alternes dans la meme minute : ce qui differe est le protocole, pas l'heure.

Si l'hypothese tient, la regle est connue et la PR peut proposer une correction complete. Sinon
il faudra ecrire que le coucher de soleil vaut `264` ou `265` sans savoir pourquoi — et ne rien
ajouter a la table.

ECRITURE. `wudsk` et `wulgt`, restaures. Aucun son.
"""
import json, ssl, sys, time, urllib.request
sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
REPET = 3

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

def repos(ip, t=12):
    put(ip, "wudsk", {"onoff": False})
    put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
    time.sleep(t)

try:
    for tour in range(1, REPET + 1):
        # A — coucher de soleil seul, rien avant.
        repos(ip)
        depart, _ = etat(ip)
        put(ip, "wudsk", {"onoff": True}); time.sleep(2.5)
        v, b = etat(ip)
        print(f"  tour {tour}  A. sunset seul                depart={depart} -> "
              f"wusts={v} bits={b}", flush=True)
        res["essais"].append({"cas": "sunset seul", "tour": tour, "depart": depart,
                              "wusts": v, "bits": b})
        put(ip, "wudsk", {"onoff": False})

        # B — lampe allumée puis éteinte, puis coucher de soleil : le protocole de 19 h.
        repos(ip)
        put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(1.2)
        put(ip, "wulgt", {"onoff": False}); time.sleep(15)
        depart, _ = etat(ip)
        put(ip, "wudsk", {"onoff": True}); time.sleep(2.5)
        v, b = etat(ip)
        print(f"  tour {tour}  B. lampe avant, puis sunset   depart={depart} -> "
              f"wusts={v} bits={b}", flush=True)
        res["essais"].append({"cas": "lampe avant", "tour": tour, "depart": depart,
                              "wusts": v, "bits": b})
        put(ip, "wudsk", {"onoff": False})
finally:
    put(ip, "wudsk", {"onoff": False})
    put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")), "ngtlt": bool(lgt0.get("ngtlt")),
                      "ltlvl": ltlvl})
    time.sleep(10)
    v, _ = etat(ip)
    print(f"\n--- restauration : wusts={v}", flush=True)
    res["restauration"] = {"wusts": v}
    nom = f"prealable-bit0-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
    print("Releve ->", nom, flush=True)
