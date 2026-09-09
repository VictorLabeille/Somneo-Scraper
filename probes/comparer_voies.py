"""264 ou 265 ? Comparer l'ecriture directe et `pysomneo`, dans la meme minute.

Le 2026-09-09 a 19 h, neuf sondes ont donne **264** pour un coucher de soleil (bits 3, 8), six
fois, dans deux contextes de depart. A 21 h 56, la branche `ai-improvements` pilotee par
`pysomneo` a donne **265** (bits 0, 3, 8) — valeur jamais vue, absente de la table d'origine
comme du correctif propose. Si le coucher de soleil peut valoir les deux, **ajouter `264` seul
ne corrige rien** et la PR est a revoir.

Deux explications possibles, et une seule mesure les separe :
  (a) la voie d'ecriture : `pysomneo` ferait autre chose qu'un `PUT wudsk {"onoff": true}` ;
  (b) le contexte : quelque chose a change dans l'appareil entre 19 h et 22 h.

On alterne donc les deux voies sur la meme periode, trois fois chacune. Meme appareil, memes
minutes : ce qui differe est la voie, pas l'heure.

ECRITURE. `wudsk` uniquement, restaure. Aucun son.
"""
import asyncio, json, ssl, sys, time, urllib.request
sys.path.insert(0, "asyncpkg"); sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context(); CTX.check_hostname = False; CTX.verify_mode = ssl.CERT_NONE

def put_direct(ip, port, payload):
    req = urllib.request.Request(f"https://{ip}/di/v1/products/1/{port}",
        data=json.dumps(payload).encode(), method="PUT",
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=15, context=CTX).read()
    except Exception as e:
        print("      PUT ->", type(e).__name__)
    time.sleep(0.2)

def lire(ip, port="wusts"):
    b = (get(ip, 1, port).get("body") or {}); time.sleep(0.2)
    return b

def etat(ip, essais=4):
    """Lit `wusts`, en réessayant : une collision rend None et fausserait la comparaison."""
    for _ in range(essais):
        v = lire(ip).get("wusts")
        if v is not None:
            return v, [i for i in range(16) if v >> i & 1]
        time.sleep(1.5)
    return None, None

def repos(ip):
    put_direct(ip, "wudsk", {"onoff": False})
    put_direct(ip, "wulgt", {"onoff": False, "ngtlt": False})
    time.sleep(12)

async def via_pysomneo(ip):
    for m in [m for m in list(sys.modules) if m.startswith("pysomneo")]:
        del sys.modules[m]
    from pysomneo import Somneo
    s = Somneo(ip)
    try:
        await s.toggle_sunset(True)
        await asyncio.sleep(2.5)
        v, b = etat(ip)                       # lecture directe : pas de délai de fetch_data
        dsk = lire(ip, "wudsk")
        await s.toggle_sunset(False)
        return v, b, dsk.get("onoff")
    finally:
        try: await s._client.close()
        except Exception: pass

async def main():
    ip = discover(); print("Somneo :", ip, flush=True)
    dsk0 = lire(ip, "wudsk")
    res = {"debut": time.time(), "initial": dsk0, "essais": []}
    try:
        for tour in (1, 2, 3):
            repos(ip)
            depart, _ = etat(ip)
            put_direct(ip, "wudsk", {"onoff": True})
            time.sleep(2.5)
            v, b = etat(ip)
            on = lire(ip, "wudsk").get("onoff")
            print(f"  tour {tour}  DIRECT     depart={depart} -> wusts={v} bits={b} "
                  f"(wudsk.onoff={on})", flush=True)
            res["essais"].append({"voie": "direct", "tour": tour, "depart": depart,
                                  "wusts": v, "bits": b, "onoff": on})
            put_direct(ip, "wudsk", {"onoff": False})

            repos(ip)
            depart, _ = etat(ip)
            v, b, on = await via_pysomneo(ip)
            print(f"  tour {tour}  PYSOMNEO   depart={depart} -> wusts={v} bits={b} "
                  f"(wudsk.onoff={on})", flush=True)
            res["essais"].append({"voie": "pysomneo", "tour": tour, "depart": depart,
                                  "wusts": v, "bits": b, "onoff": on})
    finally:
        put_direct(ip, "wudsk", {"onoff": bool(dsk0.get("onoff"))})
        put_direct(ip, "wulgt", {"onoff": False, "ngtlt": False})
        time.sleep(10)
        v, _ = etat(ip)
        print(f"\n--- restauration : wusts={v}", flush=True)
        res["restauration"] = {"wusts": v}
        nom = f"comparer-voies-{time.strftime('%Y%m%dT%H%M%S')}.json"
        json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
        print("Releve ->", nom, flush=True)

asyncio.run(main())
