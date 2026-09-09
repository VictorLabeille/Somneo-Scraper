"""Le lecteur audio sur une VRAIE source sonore — aux, puis FM.

Le premier essai a echoue a mesurer ce qu'il visait : `wuply.snddv` valait `"rlx"`, donc allumer
le lecteur demarrait RelaxBreathing (320, bits 6 et 8) au lieu de jouer du son. On force ici la
source : `aux` (silencieuse si rien n'est branche) puis `fmr` (preset 1, 87.50 — du souffle).

Ce qu'on cherche : le bit 9 (son) **sans** le bit 8 (lumiere). Aucune entree de la table
`STATUS` de `pysomneo` ne couvre cette combinaison, alors qu'ecouter la radio est l'usage le
plus ordinaire du reveil apres la lampe.

ECRITURE. `wuply` seulement. Volume 1. Restaure la source et le volume d'origine.
"""
import json, ssl, time, urllib.request
from somneo_probe import discover, get
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

def put(ip, port, payload):
    try:
        req = urllib.request.Request(f"https://{ip}/di/v1/products/1/{port}",
            data=json.dumps(payload).encode(), method="PUT",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15, context=CTX).read()
    except Exception as e:
        print("   PUT", port, "->", type(e).__name__, e)
    time.sleep(0.2)

def st(ip):
    v = (get(ip, 1, "wusts").get("body") or {}).get("wusts"); time.sleep(0.2)
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])

ip = discover(); print("Somneo :", ip)
ply0 = (get(ip, 1, "wuply").get("body") or {}); time.sleep(0.2)
lgt0 = (get(ip, 1, "wulgt").get("body") or {}); time.sleep(0.2)
ltlvl = lgt0.get("ltlvl", 15)
res = {"initial": {"wuply": ply0, "wulgt": lgt0}, "essais": []}

try:
    for source, canal in (("aux", ""), ("fmr", "1")):
        for tour in (1, 2, 3):
            put(ip, "wuply", {"onoff": False})
            put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
            time.sleep(10)
            depart, _ = st(ip)
            put(ip, "wuply", {"onoff": True, "snddv": source, "sndch": canal,
                              "sdvol": 1, "sndss": 0})
            time.sleep(3)
            v, b = st(ip)
            lu = (get(ip, 1, "wuply").get("body") or {}); time.sleep(0.2)
            print(f"   {source:<4} tour {tour}  depart={str(depart):<4} -> wusts={str(v):<5} "
                  f"bits={b}  (onoff={lu.get('onoff')} snddv={lu.get('snddv')!r} "
                  f"sdvol={lu.get('sdvol')})")
            res["essais"].append({"source": source, "tour": tour, "depart": depart,
                                  "wusts": v, "bits": b, "onoff_relu": lu.get("onoff"),
                                  "snddv_relu": lu.get("snddv")})
            # Lampe par-dessus le son : bits 8 et 9 ensemble ?
            if tour == 1:
                put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(2)
                v2, b2 = st(ip)
                print(f"        + lampe -> wusts={str(v2):<5} bits={b2}")
                res["essais"].append({"source": source + "+lampe", "wusts": v2, "bits": b2})
                put(ip, "wulgt", {"onoff": False})
            put(ip, "wuply", {"onoff": False}); time.sleep(2)
finally:
    put(ip, "wuply", {"onoff": bool(ply0.get("onoff")), "sdvol": ply0.get("sdvol", 8),
                      "snddv": ply0.get("snddv", "rlx"), "sndch": ply0.get("sndch", "1"),
                      "sndss": ply0.get("sndss", 0)})
    put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")), "ngtlt": bool(lgt0.get("ngtlt")),
                      "ltlvl": ltlvl})
    time.sleep(10)
    v, _ = st(ip)
    p1 = (get(ip, 1, "wuply").get("body") or {}); time.sleep(0.2)
    l1 = (get(ip, 1, "wulgt").get("body") or {})
    ecarts = {k: (ply0.get(k), p1.get(k)) for k in ("onoff", "sdvol", "snddv", "sndch")
              if ply0.get(k) != p1.get(k)}
    ecarts.update({("wulgt." + k): (lgt0.get(k), l1.get(k)) for k in ("onoff", "ngtlt", "ltlvl")
                   if lgt0.get(k) != l1.get(k)})
    print(f"--- restauration : wusts={v}  ecarts={ecarts or 'aucun'}")
    res["restauration"] = {"wusts": v, "wuply": p1, "wulgt": l1, "ecarts": str(ecarts)}
    nom = f"lecteur-sources-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
    print("Releve ->", nom)
