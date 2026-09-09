"""Le lecteur audio, la lampe, et leur combinaison — les etats que `wusts` prend encore.

Le lecteur (`wuply`) est l'usage le plus courant du reveil apres la lampe, et il n'a jamais ete
mesure. S'il porte le bit 9 (son) sans le bit 8 (lumiere), il produit une valeur qu'aucune
entree de la table `STATUS` de `pysomneo` ne couvre — un trou de plus, et le plus frequent.

On teste aussi la combinaison lampe + lecteur : bits 8 et 9 ensemble, hors alarme.

ECRITURE. `wuply` et `wulgt`. Volume 1. Restaure tout.
"""
import json, ssl, time, urllib.request, urllib.error
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

def repos():
    put(ip, "wuply", {"onoff": False})
    put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
    time.sleep(10)

try:
    # 1. Le lecteur seul, depuis le repos puis dans le transitoire.
    for libelle, delai in (("depuis le repos", 12), ("dans le transitoire", 2)):
        for tour in (1, 2, 3):
            repos()
            put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(1.2)
            put(ip, "wulgt", {"onoff": False}); time.sleep(delai)
            depart, _ = st(ip)
            put(ip, "wuply", {"onoff": True, "sdvol": 1, "snddv": ply0.get("snddv", "rlx"),
                              "sndch": ply0.get("sndch", "1"), "sndss": ply0.get("sndss", 0)})
            time.sleep(2.5)
            v, b = st(ip)
            lu = (get(ip, 1, "wuply").get("body") or {}); time.sleep(0.2)
            print(f"   lecteur {libelle:<21} depart={str(depart):<4} -> wusts={str(v):<5} "
                  f"bits={b} (onoff={lu.get('onoff')} snddv={lu.get('snddv')!r})")
            res["essais"].append({"quoi": "lecteur", "contexte": libelle, "depart": depart,
                                  "wusts": v, "bits": b, "onoff_relu": lu.get("onoff")})
            put(ip, "wuply", {"onoff": False}); time.sleep(2)

    # 2. Lampe + lecteur : bits 8 et 9 ensemble, hors alarme.
    for tour in (1, 2):
        repos()
        put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(1.5)
        v1, b1 = st(ip)
        put(ip, "wuply", {"onoff": True, "sdvol": 1, "snddv": ply0.get("snddv", "rlx"),
                          "sndch": ply0.get("sndch", "1"), "sndss": ply0.get("sndss", 0)})
        time.sleep(2.5)
        v2, b2 = st(ip)
        print(f"   lampe seule -> {v1} {b1}   PUIS lampe+lecteur -> {v2} {b2}")
        res["essais"].append({"quoi": "lampe+lecteur", "lampe_seule": v1, "bits_lampe": b1,
                              "combine": v2, "bits_combine": b2})
        put(ip, "wuply", {"onoff": False}); put(ip, "wulgt", {"onoff": False}); time.sleep(2)

    # 3. Le transitoire suit-il l'extinction du lecteur ?
    repos()
    put(ip, "wuply", {"onoff": True, "sdvol": 1, "snddv": ply0.get("snddv", "rlx"),
                      "sndch": ply0.get("sndch", "1"), "sndss": ply0.get("sndss", 0)})
    time.sleep(2)
    put(ip, "wuply", {"onoff": False})
    t0 = time.monotonic(); trace = []
    while time.monotonic() - t0 < 15:
        v, _ = st(ip)
        trace.append({"t": round(time.monotonic() - t0, 2), "wusts": v})
        if v == 1: break
        time.sleep(0.5)
    print(f"   extinction du lecteur -> {[e['wusts'] for e in trace]}")
    res["essais"].append({"quoi": "extinction lecteur", "trace": trace})
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
    nom = f"lecteur-wusts-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
    print("Releve ->", nom)
