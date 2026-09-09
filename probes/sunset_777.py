"""776 contre 777, et un motif sonore reconnaissable a l'oreille.

VOLET 1 — expliquer `777`. Apparu une fois le 2026-09-09 : coucher de soleil sonore dont la
source etait **deja configuree**, le PUT ne portant que `onoff`. Le `776` du meme soir venait
d'un PUT qui configurait la source ET demarrait dans le meme appel. Si l'ecart tient a cela,
toute la table de `pysomneo` est expliquee, snooze mis a part.

VOLET 2 — le bit 9 : source engagee, ou son emis ? Le proprietaire n'a entendu qu'« un son
d'une ou deux secondes » sur deux fenetres de 4 s a volume 10. On emet donc un motif
identifiable sans voir le journal : trois salves courtes de radio, un silence, une salve longue
de son de coucher de soleil. Ce qu'il decrira dit lequel des deux emet reellement.

ECRITURE. `wuply`, `wudsk`, `wulgt`. Volume 12. Restaure tout.
"""
import json, ssl, time, urllib.request
from somneo_probe import discover, get
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
VOL = 12

def put(ip, port, payload):
    try:
        req = urllib.request.Request(f"https://{ip}/di/v1/products/1/{port}",
            data=json.dumps(payload).encode(), method="PUT",
            headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15, context=CTX).read()
    except Exception as e:
        print("   PUT", port, "->", type(e).__name__, e, flush=True)
    time.sleep(0.2)

def st(ip):
    v = (get(ip, 1, "wusts").get("body") or {}).get("wusts"); time.sleep(0.2)
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])

ip = discover(); print("Somneo :", ip, flush=True)
ply0=(get(ip,1,"wuply").get("body") or {}); time.sleep(0.2)
lgt0=(get(ip,1,"wulgt").get("body") or {}); time.sleep(0.2)
dsk0=(get(ip,1,"wudsk").get("body") or {}); time.sleep(0.2)
ltlvl=lgt0.get("ltlvl",15)
res={"initial":{"wuply":ply0,"wulgt":lgt0,"wudsk":dsk0},"volet1":[],"volet2":[]}

def repos(t=10):
    put(ip,"wuply",{"onoff":False}); put(ip,"wudsk",{"onoff":False}); put(ip,"wurlx",{"onoff":False})
    put(ip,"wulgt",{"onoff":False,"ngtlt":False,"ltlvl":ltlvl}); time.sleep(t)

try:
    print("\n== VOLET 1 : 776 contre 777 ==", flush=True)
    # A : la source est configuree DANS le PUT qui demarre.
    for tour in (1,2,3):
        repos()
        put(ip,"wudsk",{"snddv":"off","sndch":"","sndlv":1}); time.sleep(1)
        put(ip,"wudsk",{"onoff":True,"snddv":"dus","sndch":"1","sndlv":1}); time.sleep(2.5)
        v,b=st(ip)
        print(f"   source dans le PUT      -> wusts={v} bits={b}", flush=True)
        res["volet1"].append({"cas":"source dans le PUT","wusts":v,"bits":b})
        put(ip,"wudsk",{"onoff":False}); time.sleep(2)
    # B : la source est DEJA en place, le PUT ne porte que onoff.
    for tour in (1,2,3):
        repos()
        put(ip,"wudsk",{"snddv":"dus","sndch":"1","sndlv":1}); time.sleep(2)
        put(ip,"wudsk",{"onoff":True}); time.sleep(2.5)
        v,b=st(ip)
        print(f"   source deja en place    -> wusts={v} bits={b}", flush=True)
        res["volet1"].append({"cas":"source deja en place","wusts":v,"bits":b})
        put(ip,"wudsk",{"onoff":False}); time.sleep(2)

    print(f"\n== VOLET 2 : motif sonore, volume {VOL} ==", flush=True)
    repos()
    print("   3 salves de RADIO de 3 s, separees de 2 s", flush=True)
    for i in (1,2,3):
        put(ip,"wuply",{"onoff":True,"snddv":"fmr","sndch":"1","sdvol":VOL,"sndss":0})
        if i==1:
            time.sleep(1.5); v,b=st(ip); res["volet2"].append({"quoi":"FM","wusts":v,"bits":b})
            print(f"      wusts={v} bits={b}", flush=True); time.sleep(1.5)
        else:
            time.sleep(3)
        put(ip,"wuply",{"onoff":False}); time.sleep(2)
    print("   silence de 8 s", flush=True); time.sleep(8)
    print("   1 salve LONGUE de son de coucher de soleil, 8 s", flush=True)
    put(ip,"wudsk",{"onoff":True,"snddv":"dus","sndch":"1","sndlv":VOL})
    time.sleep(1.5); v,b=st(ip); res["volet2"].append({"quoi":"sunset sonore","wusts":v,"bits":b})
    print(f"      wusts={v} bits={b}", flush=True)
    time.sleep(6.5)
    put(ip,"wudsk",{"onoff":False})
finally:
    put(ip,"wuply",{"onoff":bool(ply0.get("onoff")),"sdvol":ply0.get("sdvol",8),
                    "snddv":ply0.get("snddv","rlx"),"sndch":ply0.get("sndch","1"),
                    "sndss":ply0.get("sndss",0)})
    put(ip,"wudsk",{"onoff":bool(dsk0.get("onoff")),"snddv":dsk0.get("snddv","off"),
                    "sndch":dsk0.get("sndch",""),"sndlv":dsk0.get("sndlv",12)})
    put(ip,"wulgt",{"onoff":bool(lgt0.get("onoff")),"ngtlt":bool(lgt0.get("ngtlt")),"ltlvl":ltlvl})
    time.sleep(10)
    v,_=st(ip)
    p1=(get(ip,1,"wuply").get("body") or {}); time.sleep(0.2)
    d1=(get(ip,1,"wudsk").get("body") or {}); time.sleep(0.2)
    l1=(get(ip,1,"wulgt").get("body") or {})
    ec={}
    for nom,a,b_,ch in (("wuply",ply0,p1,("onoff","sdvol","snddv","sndch")),
                        ("wudsk",dsk0,d1,("onoff","snddv","sndch","sndlv")),
                        ("wulgt",lgt0,l1,("onoff","ngtlt","ltlvl"))):
        for c in ch:
            if a.get(c)!=b_.get(c): ec[f"{nom}.{c}"]=(a.get(c),b_.get(c))
    print(f"\n--- restauration : wusts={v}  ecarts={ec or 'aucun'}", flush=True)
    res["restauration"]={"wusts":v,"ecarts":str(ec)}
    nom=f"sunset-777-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res,open(nom,"w"),ensure_ascii=False,indent=2)
    print("Releve ->",nom, flush=True)
