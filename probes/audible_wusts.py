"""Le bit 9 marque-t-il un son REELLEMENT emis ? Et les dernieres combinaisons.

Toute la campagne du 2026-09-09 a tourne a `sdvol`/`sndlv` = 1, et le proprietaire, present
dans la piece, n'a rien entendu — alors que l'appareil a des haut-parleurs integres. Deux
lectures possibles, et elles ne disent pas la meme chose du bit 9 :
  (a) volume 1 sur une echelle qui monte a 25 : le son sortait, inaudible ;
  (b) le bit 9 se leve des qu'une source est engagee, sans emission.

On tranche a l'oreille : volume 10, sources reelles, annonces au fil du log. La doc affirme
pour l'instant (b) sur la foi de `aux` sans cable — si (a) est vrai, cette affirmation est trop
forte et doit etre corrigee.

Trois combinaisons restantes au passage :
  - veilleuse + lecteur : le bit 8 s'ajoute-t-il comme avec la lampe (769) ?
  - afficheur (`dspon`) + lampe : le bit 1 sort-il enfin de la ou on ne l'attend pas ?
  - coucher de soleil + lecteur : deux sources sonores a la fois.

ECRITURE. `wuply`, `wulgt`, `wudsk`, `wusts` (afficheur). Volume 10, quelques secondes.
Restaure tout.
"""
import json, ssl, time, urllib.request
from somneo_probe import discover, get
CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
VOL = 10

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

ip = discover(); print("Somneo :", ip, flush=True)
ply0 = (get(ip,1,"wuply").get("body") or {}); time.sleep(0.2)
lgt0 = (get(ip,1,"wulgt").get("body") or {}); time.sleep(0.2)
dsk0 = (get(ip,1,"wudsk").get("body") or {}); time.sleep(0.2)
sts0 = (get(ip,1,"wusts").get("body") or {}); time.sleep(0.2)
ltlvl = lgt0.get("ltlvl", 15)
res = {"initial": {"wuply": ply0, "wulgt": lgt0, "wudsk": dsk0, "wusts": sts0}, "essais": []}

def repos():
    put(ip,"wuply",{"onoff":False}); put(ip,"wudsk",{"onoff":False}); put(ip,"wurlx",{"onoff":False})
    put(ip,"wulgt",{"onoff":False,"ngtlt":False,"ltlvl":ltlvl}); time.sleep(10)

try:
    print(f"\n== A. FM preset 1 (87.50) au volume {VOL} — 6 s ==", flush=True)
    repos()
    put(ip,"wuply",{"onoff":True,"snddv":"fmr","sndch":"1","sdvol":VOL,"sndss":0})
    time.sleep(2); v,b = st(ip)
    print(f"   wusts={v} bits={b}  -> ECOUTE MAINTENANT (4 s)", flush=True)
    time.sleep(4)
    res["essais"].append({"quoi":f"FM volume {VOL}","wusts":v,"bits":b})
    put(ip,"wuply",{"onoff":False}); time.sleep(2)

    print(f"\n== B. son de coucher de soleil (dus/1) au volume {VOL} — 6 s ==", flush=True)
    repos()
    put(ip,"wudsk",{"onoff":True,"snddv":"dus","sndch":"1","sndlv":VOL})
    time.sleep(2); v,b = st(ip)
    print(f"   wusts={v} bits={b}  -> ECOUTE MAINTENANT (4 s)", flush=True)
    time.sleep(4)
    res["essais"].append({"quoi":f"sunset sonore volume {VOL}","wusts":v,"bits":b})
    put(ip,"wudsk",{"onoff":False}); time.sleep(2)

    print("\n== C. veilleuse + lecteur ==", flush=True)
    repos()
    put(ip,"wulgt",{"ngtlt":True}); time.sleep(1.5); v1,b1 = st(ip)
    put(ip,"wuply",{"onoff":True,"snddv":"aux","sndch":"","sdvol":1,"sndss":0}); time.sleep(2.5)
    v2,b2 = st(ip)
    print(f"   veilleuse seule -> {v1} {b1}   + lecteur -> {v2} {b2}", flush=True)
    res["essais"].append({"quoi":"veilleuse+lecteur","veilleuse":v1,"bits_v":b1,"combine":v2,"bits_c":b2})
    put(ip,"wuply",{"onoff":False}); put(ip,"wulgt",{"ngtlt":False}); time.sleep(2)

    print("\n== D. afficheur (dspon) + lampe ==", flush=True)
    repos()
    put(ip,"wusts",{"dspon":True,"brght":sts0.get("brght",1)}); time.sleep(2)
    v1,b1 = st(ip)
    put(ip,"wulgt",{"onoff":True,"ltlvl":3}); time.sleep(2); v2,b2 = st(ip)
    print(f"   dspon seul -> {v1} {b1}   + lampe -> {v2} {b2}", flush=True)
    res["essais"].append({"quoi":"dspon+lampe","dspon":v1,"bits_d":b1,"combine":v2,"bits_c":b2})
    put(ip,"wulgt",{"onoff":False}); put(ip,"wusts",{"dspon":bool(sts0.get("dspon")),"brght":sts0.get("brght",1)})
    time.sleep(2)

    print("\n== E. coucher de soleil + lecteur ==", flush=True)
    repos()
    put(ip,"wudsk",{"onoff":True}); time.sleep(2); v1,b1 = st(ip)
    put(ip,"wuply",{"onoff":True,"snddv":"aux","sndch":"","sdvol":1,"sndss":0}); time.sleep(2.5)
    v2,b2 = st(ip)
    print(f"   sunset seul -> {v1} {b1}   + lecteur -> {v2} {b2}", flush=True)
    res["essais"].append({"quoi":"sunset+lecteur","sunset":v1,"bits_s":b1,"combine":v2,"bits_c":b2})
    put(ip,"wuply",{"onoff":False}); put(ip,"wudsk",{"onoff":False})
finally:
    put(ip,"wuply",{"onoff":bool(ply0.get("onoff")),"sdvol":ply0.get("sdvol",8),
                    "snddv":ply0.get("snddv","rlx"),"sndch":ply0.get("sndch","1"),
                    "sndss":ply0.get("sndss",0)})
    put(ip,"wudsk",{"onoff":bool(dsk0.get("onoff")),"snddv":dsk0.get("snddv","off"),
                    "sndch":dsk0.get("sndch",""),"sndlv":dsk0.get("sndlv",12)})
    put(ip,"wulgt",{"onoff":bool(lgt0.get("onoff")),"ngtlt":bool(lgt0.get("ngtlt")),"ltlvl":ltlvl})
    put(ip,"wusts",{"dspon":bool(sts0.get("dspon")),"brght":sts0.get("brght",1)})
    time.sleep(10)
    v,_ = st(ip)
    p1=(get(ip,1,"wuply").get("body") or {}); time.sleep(0.2)
    d1=(get(ip,1,"wudsk").get("body") or {}); time.sleep(0.2)
    l1=(get(ip,1,"wulgt").get("body") or {}); time.sleep(0.2)
    s1=(get(ip,1,"wusts").get("body") or {})
    ec={}
    for nom,a,b_,ch in (("wuply",ply0,p1,("onoff","sdvol","snddv","sndch")),
                        ("wudsk",dsk0,d1,("onoff","snddv","sndch","sndlv")),
                        ("wulgt",lgt0,l1,("onoff","ngtlt","ltlvl")),
                        ("wusts",sts0,s1,("dspon","brght"))):
        for c in ch:
            if a.get(c)!=b_.get(c): ec[f"{nom}.{c}"]=(a.get(c),b_.get(c))
    print(f"\n--- restauration : wusts={v}  ecarts={ec or 'aucun'}", flush=True)
    res["restauration"]={"wusts":v,"ecarts":str(ec)}
    nom=f"audible-{time.strftime('%Y%m%dT%H%M%S')}.json"
    json.dump(res,open(nom,"w"),ensure_ascii=False,indent=2)
    print("Releve ->",nom, flush=True)
