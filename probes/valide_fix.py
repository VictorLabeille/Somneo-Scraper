"""Le correctif de `STATUS` fait-il ce qu'il annonce, dans `pysomneo` lui-meme ?

Toute la campagne a mesure ce que l'APPAREIL renvoie dans `wusts`. Elle n'a jamais verifie ce
que `pysomneo` en fait — or c'est cela que la PR corrige : la valeur de `somneo_status`, publiee
telle quelle par le capteur `sensor.somneo_alarm_status` de l'integration Home Assistant.

On execute donc la branche `ai-improvements` deux fois sur les memes etats reels, table
d'origine puis table corrigee, et on compare ce que `fetch_data()` produit :

  coucher de soleil lance   attendu avant : "unknown"   apres : "sunset"
  transitoire d'extinction  attendu avant : "sunset"    apres : "unknown"

Le second cas est le plus important : c'est le faux positif que subissent aujourd'hui les
automations qui declenchent sur `somneo_status == "sunset"`.

ECRITURE. `wulgt` et `wudsk`, restaures. Aucun son. Lecture de `somneo_status` par la
bibliotheque, pas par une requete directe : c'est tout l'interet du test.
"""
import asyncio, json, sys, time
sys.path.insert(0, "asyncpkg")
sys.path.insert(0, ".")
import importlib
from somneo_probe import discover

async def run(ip, etiquette):
    # Rechargement complet : const.py vient d'etre modifie sur le disque.
    for mod in [m for m in list(sys.modules) if m.startswith("pysomneo")]:
        del sys.modules[mod]
    from pysomneo import Somneo
    from pysomneo.const import STATUS
    print(f"\n=== {etiquette} — STATUS a {len(STATUS)} entrees, "
          f"2->{STATUS.get(2)!r}  264->{STATUS.get(264)!r}")

    s = Somneo(ip)
    out = {}
    try:
        # 1. Coucher de soleil lance.
        await s._client.modify_sunset({"onoff": True})
        await asyncio.sleep(2.5)
        await s.fetch_data(force_slow_refresh=True)
        brut = s.alarm_status.get("wusts")
        etat = s.data.get("somneo_status")
        print(f"   coucher de soleil   wusts={brut:<5} somneo_status={etat!r}")
        out["sunset"] = {"wusts": brut, "somneo_status": etat}

        # 2. Extinction : on lit DANS le transitoire (il dure ~7,5 s).
        await s._client.modify_sunset({"onoff": False})
        await asyncio.sleep(1.5)
        await s.fetch_data(force_slow_refresh=True)
        brut = s.alarm_status.get("wusts")
        etat = s.data.get("somneo_status")
        print(f"   transitoire         wusts={brut:<5} somneo_status={etat!r}")
        out["transitoire"] = {"wusts": brut, "somneo_status": etat}
    finally:
        try:
            await s._client.modify_sunset({"onoff": False})
            await s._client.close()
        except Exception:
            pass
    await asyncio.sleep(10)
    return out

def patcher(applique):
    p = "asyncpkg/pysomneo/const.py"
    s = open(p, encoding="utf-8").read()
    origine = '    1: "off",\n    2: "sunset",\n    257: "light-on",\n'
    corrige = '    1: "off",\n    257: "light-on",\n    264: "sunset",\n'
    avant, apres = (origine, corrige) if applique else (corrige, origine)
    if avant in s:
        open(p, "w", encoding="utf-8").write(s.replace(avant, apres, 1))

async def main():
    ip = discover()
    print("Somneo :", ip)
    res = {"debut": time.time()}
    try:
        patcher(False)
        res["avant"] = await run(ip, "TABLE D'ORIGINE")
        patcher(True)
        res["apres"] = await run(ip, "TABLE CORRIGEE (la PR)")
    finally:
        patcher(False)   # on laisse la branche telle qu'elle est en amont
        nom = f"valide-fix-{time.strftime('%Y%m%dT%H%M%S')}.json"
        json.dump(res, open(nom, "w"), ensure_ascii=False, indent=2)
        print(f"\nReleve -> {nom}")

asyncio.run(main())
