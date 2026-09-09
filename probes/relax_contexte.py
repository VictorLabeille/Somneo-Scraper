"""RelaxBreathing suit-il la regle du bit de contexte ? Dernier trou avant la PR."""
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
        print("   PUT", port, "->", type(e).__name__)
    time.sleep(0.2)

def st(ip):
    v = (get(ip, 1, "wusts").get("body") or {}).get("wusts"); time.sleep(0.2)
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])

ip = discover(); print("Somneo :", ip)
rlx0 = (get(ip, 1, "wurlx").get("body") or {}); time.sleep(0.2)
lgt0 = (get(ip, 1, "wulgt").get("body") or {}); time.sleep(0.2)
ltlvl = lgt0.get("ltlvl", 15)
res = {"initial": {"wurlx": rlx0, "wulgt": lgt0}, "essais": []}
try:
    for libelle, delai in (("depuis le repos", 12), ("dans le transitoire", 2)):
        for tour in (1, 2, 3):
            put(ip, "wurlx", {"onoff": False})
            put(ip, "wulgt", {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
            time.sleep(1)
            put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(1.2)
            put(ip, "wulgt", {"onoff": False}); time.sleep(delai)
            depart, _ = st(ip)
            put(ip, "wurlx", {"onoff": True, "sndlv": 1}); time.sleep(2.5)
            v, b = st(ip)
            print(f"   {libelle:<21} depart={str(depart):<4} -> wusts={str(v):<5} bits={b}")
            res["essais"].append({"contexte": libelle, "depart": depart, "wusts": v, "bits": b})
            put(ip, "wurlx", {"onoff": False}); time.sleep(2)
finally:
    put(ip, "wurlx", {"onoff": bool(rlx0.get("onoff")), "sndlv": rlx0.get("sndlv", 8)})
    put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")), "ngtlt": bool(lgt0.get("ngtlt")), "ltlvl": ltlvl})
    time.sleep(8)
    v, _ = st(ip); l = (get(ip, 1, "wulgt").get("body") or {})
    print(f"--- restauration : wusts={v} onoff={l.get('onoff')} ngtlt={l.get('ngtlt')} ltlvl={l.get('ltlvl')}")
    res["restauration"] = {"wusts": v, "wulgt": l}
    nom = f"relax-contexte-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
    print("Releve ->", nom)
