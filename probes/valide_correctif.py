"""Le correctif complet fait-il ce qu'il annonce, et a partir de quand le bit 0 revient-il ?

Deux volets, dans cet ordre parce que le premier decide de la PR.

VOLET 1 — LA VALIDATION. La lecon de la soiree : un correctif ne se propose pas sans l'avoir vu
tourner. Le premier essai (`valide_fix.py`, table a `264` seul) avait montre que le patch ne
corrigeait **rien** — l'appareil renvoyait `265`. La table corrigee porte desormais `2` renomme,
`264` et `265`. On execute la branche `ai-improvements` avec l'ancienne puis la nouvelle table,
sur les memes etats reels, et on compare ce que `somneo_status` produit.

VOLET 2 — LA BORNE. Le bit 0 est mesure present a 2 min et 5 min sans lumiere, absent juste
apres une lampe. Entre les deux, rien. Dire « deux minutes » dans un message public sans avoir
teste 30 s et 60 s, c'est offrir la borne a verifier au premier lecteur venu.

ECRITURE. `wudsk` et `wulgt`, restaures. Aucun son.
"""
import asyncio, json, ssl, sys, time, urllib.request
sys.path.insert(0, "asyncpkg"); sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE
ORIGINE = '    1: "off",\n    2: "sunset",\n    257: "light-on",\n'
CORRIGE = ('    1: "off",\n    2: "switching-off",\n    257: "light-on",\n'
           '    264: "sunset",\n    265: "sunset",\n')

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

def brut(ip):
    v = lire(ip).get("wusts")
    return v, (None if v is None else [i for i in range(16) if v >> i & 1])

def patcher(applique):
    p = "asyncpkg/pysomneo/const.py"
    s = open(p, encoding="utf-8").read()
    avant, apres = (ORIGINE, CORRIGE) if applique else (CORRIGE, ORIGINE)
    if avant in s:
        open(p, "w", encoding="utf-8").write(s.replace(avant, apres, 1))
        return True
    return CORRIGE in s if applique else ORIGINE in s

async def lire_par_pysomneo(ip):
    """Ce que la bibliotheque publie : c'est cela que le capteur HA affiche."""
    for m in [m for m in list(sys.modules) if m.startswith("pysomneo")]:
        del sys.modules[m]
    from pysomneo import Somneo
    s = Somneo(ip)
    try:
        await s.fetch_data(force_slow_refresh=True)
        return s.alarm_status.get("wusts"), s.data.get("somneo_status")
    finally:
        try: await s._client.close()
        except Exception: pass

async def volet1(ip, res):
    for applique, etiquette in ((False, "TABLE D'ORIGINE"), (True, "TABLE CORRIGEE (la PR)")):
        patcher(applique)
        print(f"\n=== {etiquette} ===", flush=True)
        bloc = {}
        # Coucher de soleil, appareil au repos : le cas courant.
        put(ip, "wudsk", {"onoff": False}); put(ip, "wulgt", {"onoff": False, "ngtlt": False})
        await asyncio.sleep(130)                      # > 2 min : le bit 0 doit être là
        put(ip, "wudsk", {"onoff": True}); await asyncio.sleep(2.5)
        v, etat = await lire_par_pysomneo(ip)
        print(f"   coucher de soleil    wusts={v}  somneo_status={etat!r}", flush=True)
        bloc["sunset"] = {"wusts": v, "somneo_status": etat}
        # Transitoire d'extinction.
        put(ip, "wudsk", {"onoff": False}); await asyncio.sleep(1.5)
        v, etat = await lire_par_pysomneo(ip)
        print(f"   transitoire          wusts={v}  somneo_status={etat!r}", flush=True)
        bloc["transitoire"] = {"wusts": v, "somneo_status": etat}
        res["volet1_" + ("corrige" if applique else "origine")] = bloc
        await asyncio.sleep(12)

def volet2(ip, res):
    print("\n=== VOLET 2 : a partir de quand le bit 0 revient-il ? ===", flush=True)
    out = []
    for attente in (15, 30, 60, 90, 120):
        put(ip, "wudsk", {"onoff": False})
        put(ip, "wulgt", {"onoff": True, "ltlvl": 3}); time.sleep(1.5)
        put(ip, "wulgt", {"onoff": False}); time.sleep(attente)
        put(ip, "wudsk", {"onoff": True}); time.sleep(2.5)
        v, b = brut(ip)
        marque = "  <-- bit 0 present" if v == 265 else ""
        print(f"   {attente:>3} s apres la lampe -> wusts={v} bits={b}{marque}", flush=True)
        out.append({"apres_lampe_s": attente, "wusts": v, "bits": b})
        put(ip, "wudsk", {"onoff": False}); time.sleep(2)
    res["volet2"] = out

async def main():
    ip = discover(); print("Somneo :", ip, flush=True)
    lgt0 = lire(ip, "wulgt")
    res = {"debut": time.time()}
    try:
        await volet1(ip, res)
        volet2(ip, res)
    finally:
        patcher(False)      # la branche est laissée telle qu'elle est en amont
        put(ip, "wudsk", {"onoff": False})
        put(ip, "wulgt", {"onoff": bool(lgt0.get("onoff")), "ngtlt": bool(lgt0.get("ngtlt")),
                          "ltlvl": lgt0.get("ltlvl", 15)})
        time.sleep(10)
        v, _ = brut(ip)
        print(f"\n--- restauration : wusts={v}", flush=True)
        res["restauration"] = {"wusts": v}
        nom = f"valide2-{time.strftime('%Y%m%dT%H%M%S')}.json"
        json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
        print("Releve ->", nom, flush=True)

asyncio.run(main())
