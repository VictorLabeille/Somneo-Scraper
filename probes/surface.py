"""Surface de l'API : ce qui existe au-dela de ce que l'application constructeur utilise.

Cinq volets, tous en lecture sauf le volet d'abonnement (POST, `ttl` court) :
  1. identifiants de produit 0 a 8 — seuls 0 et 1 sont documentes ;
  2. sous-ressources des ports connus (`dataupload/*`, `wualm/*`, `files/*`) ;
  3. methodes HTTP autres que GET sur un port connu ;
  4. formes d'abonnement UDP — le POST du 6 septembre a renvoye le corps du port au lieu
     d'une confirmation, donc la forme employee n'etait probablement pas la bonne ;
  5. balayage par dictionnaire de noms hors convention `wu` — `device`, `wifiui`, `fac` et
     `dataupload` prouvent qu'il en existe.
"""
import json
import ssl
import sys
import time
import urllib.request

sys.path.insert(0, ".")
from somneo_probe import discover, get

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
REDACTED = {"serial", "macaddress", "cppid", "ssid", "key", "nextkey", "password"}


def sans_secrets(o):
    if isinstance(o, dict):
        return {k: ("<retire>" if k.lower() in REDACTED else sans_secrets(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [sans_secrets(v) for v in o]
    return o


def brut(ip, methode, chemin, corps=None, entetes=None):
    url = f"https://{ip}{chemin}"
    rec = {"methode": methode, "chemin": chemin, "ok": False}
    try:
        data = json.dumps(corps).encode() if corps is not None else None
        req = urllib.request.Request(url, data=data, method=methode,
                                     headers=entetes or {})
        with urllib.request.urlopen(req, timeout=12, context=CTX) as r:
            rec.update(status=r.status, ok=True,
                       corps=r.read().decode("utf-8", errors="replace")[:250])
    except urllib.error.HTTPError as exc:
        rec.update(status=exc.code,
                   corps=exc.read().decode("utf-8", errors="replace")[:250])
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    return rec


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    out = {}

    print("1. Identifiants de produit")
    out["produits"] = []
    for n in range(0, 9):
        r = get(ip, n, "", timeout=15)
        etat = "OK" if r["ok"] else (r.get("status") or r.get("error"))
        print(f"   products/{n} -> {etat}")
        out["produits"].append({"produit": n, "status": r.get("status"),
                                "ok": r["ok"], "error": r.get("error")})
        time.sleep(0.3)

    print("\n2. Sous-ressources des ports connus")
    sous = []
    for base, suffixes in (
        ("dataupload", ["temp.1/data", "hum.1/data", "snd.1/data", "lux.1/data",
                        "event.1/data", "temp.2/data", "temp.1", "event.1", "data"]),
        ("wualm", ["aenvs", "aalms", "alctr", "prfwu", "prfnr", "snztm"]),
        ("files", ["lightthemes", "dusklightthemes", "wakeup", "winddowndusk",
                   "sounds", "themes", "list"]),
    ):
        for suf in suffixes:
            port = f"{base}/{suf}"
            r = get(ip, 1, port, timeout=12)
            if r["ok"]:
                print(f"   {port:<24} 200  {json.dumps(sans_secrets(r.get('body')))[:110]}")
            sous.append({"port": port, "status": r.get("status"), "ok": r["ok"]})
            time.sleep(0.2)
    out["sous_ressources"] = sous
    print(f"   {sum(1 for s in sous if s['ok'])}/{len(sous)} repondent")

    print("\n3. Methodes HTTP sur un port connu (wusrd)")
    out["methodes"] = []
    for m in ("HEAD", "OPTIONS", "DELETE", "PATCH", "POST"):
        r = brut(ip, m, "/di/v1/products/1/wusrd",
                 corps={} if m in ("PATCH", "POST") else None,
                 entetes={"Content-Type": "application/json"} if m in ("PATCH", "POST") else None)
        print(f"   {m:<8} -> {r.get('status') or r.get('error')}  {r.get('corps','')[:70]}")
        out["methodes"].append(r)
        time.sleep(0.3)

    print("\n4. Formes d'abonnement UDP")
    formes = [
        ("sans en-tete Condor", "/di/v1/products/1/wusrd",
         {"subscriber": "probe-1", "ttl": 60, "changeudp": 9999}, {}),
        ("avec en-tete Condor", "/di/v1/products/1/wusrd",
         {"subscriber": "probe-2", "ttl": 60, "changeudp": 9999},
         {"X-Condor-Features": "changeindication-port"}),
        ("sans changeudp", "/di/v1/products/1/wusrd",
         {"subscriber": "probe-3", "ttl": 60}, {}),
        ("sur le produit 0", "/di/v1/products/0/sub",
         {"subscriber": "probe-4", "ttl": 60, "changeudp": 9999}, {}),
    ]
    out["abonnements"] = []
    for libelle, chemin, corps, ent in formes:
        ent = dict(ent, **{"Content-Type": "application/json"})
        r = brut(ip, "POST", chemin, corps, ent)
        print(f"   {libelle:<22} -> {r.get('status') or r.get('error')}  {r.get('corps','')[:80]}")
        out["abonnements"].append({"forme": libelle, **r})
        time.sleep(0.5)
        s = get(ip, 0, "sub", timeout=12)
        if s["ok"]:
            txt = json.dumps(s.get("body"))
            print(f"      sub contient 'probe' : {'OUI' if 'probe' in txt else 'non'}")
        time.sleep(0.3)

    print("\n5. Noms hors convention wu")
    mots = """audio alarm alarms light lights lamp lamps sound sounds sensor sensors status state
    states clock time timer timers date day night sleep wake wakeup dusk dawn sunrise sunset
    radio fm preset presets volume bright display screen led leds button buttons config
    settings setting system device devices info version firmware update upgrade network wifi
    net ip dhcp cloud backend transport mem memory heap log logs debug trace stat stats
    history data upload download sync user users profile profiles theme themes curve curves
    relax breathe breath power energy temp temperature hum humidity lux light noise snd
    schedule schedules event events notify notification sub subs subscribe pairing security
    factory reset test diag diagnostic health ping echo""".split()
    trouves = []
    for mot in sorted(set(mots)):
        r = get(ip, 1, mot, timeout=10)
        if r["ok"]:
            trouves.append(mot)
            print(f"   !! {mot} REPOND : {json.dumps(sans_secrets(r.get('body')))[:110]}")
        time.sleep(0.05)
    print(f"   {len(mots)} mots essayes, {len(trouves)} reponses")
    out["dictionnaire"] = {"essayes": len(mots), "trouves": trouves}

    nom = f"surface-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
