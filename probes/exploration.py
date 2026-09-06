"""Exploration en lecture seule de l'appareil — tout ce qui n'exige pas d'ecrire.

Six volets :
  1. `pysomneo` execute contre l'appareil reel : ses propres methodes, chronometrees.
  2. Balayage cible de noms de ports non documentes (repond aux issues #16 et #6).
  3. Caracterisation du timeout de l'index du produit 1.
  4. Semantique des erreurs : quel code pour quelle faute.
  5. Cout d'un 422, pour decider si un balayage exhaustif du namespace est jouable.
  6. Port `sub` du produit 0 : abonnements actifs.

Aucune ecriture. Rien n'est imprime qui puisse porter un numero de serie ou une adresse MAC.
"""
import json
import statistics
import sys
import time

sys.path.insert(0, ".")
from somneo_probe import discover, get

REDACTED = {"serial", "macaddress", "cppid", "ssid", "key", "nextkey", "password"}


def sans_secrets(o):
    if isinstance(o, dict):
        return {k: ("<retire>" if k.lower() in REDACTED else sans_secrets(v))
                for k, v in o.items()}
    if isinstance(o, list):
        return [sans_secrets(v) for v in o]
    return o


def volet_pysomneo(ip):
    print("=" * 60)
    print("1. pysomneo 5.0.6 execute contre l'appareil")
    print("=" * 60)
    resultats = {}
    try:
        from pysomneo import Somneo
    except ImportError as exc:
        print(f"  import impossible : {exc}")
        return {"erreur": str(exc)}

    s = Somneo(ip)
    appels = [
        ("get_device_info", lambda: s.get_device_info()),
        ("fetch_data (complet)", lambda: s.fetch_data(force_slow_refresh=True)),
        ("fetch_data (rapide)", lambda: s.fetch_data()),
        ("wake_light_themes", lambda: s.wake_light_themes),
        ("dusk_light_themes", lambda: s.dusk_light_themes),
        ("wake_sound_themes", lambda: s.wake_sound_themes),
        ("dusk_sound_themes", lambda: s.dusk_sound_themes),
        ("get_alarm_details('alarm0')", lambda: s.get_alarm_details("alarm0")),
    ]
    for nom, fn in appels:
        debut = time.monotonic()
        try:
            val = fn()
            ms = (time.monotonic() - debut) * 1000
            resultats[nom] = {"ok": True, "ms": round(ms, 1), "valeur": sans_secrets(val)}
            print(f"  {nom:<32} OK    {ms:>7.0f} ms")
        except Exception as exc:
            ms = (time.monotonic() - debut) * 1000
            resultats[nom] = {"ok": False, "ms": round(ms, 1),
                              "erreur": f"{type(exc).__name__}: {exc}"}
            print(f"  {nom:<32} ECHEC {ms:>7.0f} ms  {type(exc).__name__}: {exc}")
        time.sleep(0.3)

    d = resultats.get("fetch_data (complet)", {}).get("valeur")
    if isinstance(d, dict):
        print(f"\n  Cles produites par fetch_data : {sorted(d)}")
        print(f"  somneo_status = {d.get('somneo_status')!r}"
              f"  (wusts brut attendu : 1)")
    return resultats


def volet_ports(ip):
    print("\n" + "=" * 60)
    print("2. Balayage cible de noms de ports")
    print("=" * 60)
    connus = {"wusrd", "wusts", "wualm", "wudsk", "wulgt", "wuply", "wungt", "wurlx",
              "wutmr", "wufmr", "wutms", "device", "dataupload", "wifiui", "fac"}
    candidats = [
        # convention wu + 3 lettres, familles plausibles
        "wuvol", "wudsp", "wuwiz", "wuevt", "wucfg", "wusnd", "wulog", "wuhis", "wustat",
        "wunet", "wuupd", "wusys", "wumem", "wudbg", "wuusr", "wupwr", "wubat", "wuclk",
        "wucal", "wuday", "wunit", "wusen", "wuenv", "wuair", "wuco2", "wuhum", "wutmp",
        "wulux", "wumic", "wubtn", "wukey", "wuled", "wubuz", "wuspk", "wumod", "wuscn",
        "wuprf", "wusch", "wutim", "wuzon", "wuloc", "wulng", "wuver", "wuinf", "wudat",
        # hors convention, vus ailleurs dans la gamme
        "wuwdw", "wusds", "audio", "clock", "sensor", "status", "alarm", "light", "sound",
    ]
    trouves, codes = [], {}
    for nom in candidats:
        if nom in connus:
            continue
        r = get(ip, 1, nom, timeout=10)
        code = r.get("status") or r.get("error", "?")
        codes[str(code)] = codes.get(str(code), 0) + 1
        if r.get("ok"):
            trouves.append((nom, sans_secrets(r.get("body"))))
            print(f"  !! {nom} REPOND : {json.dumps(sans_secrets(r.get('body')))[:120]}")
        time.sleep(0.25)
    print(f"  {len(candidats)} noms essayes. Repartition des reponses : {codes}")
    if not trouves:
        print("  Aucun port inconnu trouve dans cette liste.")
    return {"candidats": candidats, "trouves": trouves, "codes": codes}


def volet_index(ip):
    print("\n" + "=" * 60)
    print("3. Index du produit 1 — reproductible ?")
    print("=" * 60)
    essais = []
    for i in range(3):
        r = get(ip, 1, "", timeout=30)
        essais.append({"ok": r["ok"], "status": r.get("status"),
                       "error": r.get("error"), "ms": r["ms"]})
        print(f"  essai {i+1} : {'OK' if r['ok'] else r.get('error') or r.get('status')}"
              f"  ({r['ms']:.0f} ms)")
        time.sleep(2)
    r0 = get(ip, 0, "", timeout=30)
    print(f"  temoin produit 0 : {'OK' if r0['ok'] else r0.get('error')}  ({r0['ms']:.0f} ms)")
    return {"produit1": essais, "produit0": {"ok": r0["ok"], "ms": r0["ms"]}}


def volet_erreurs(ip):
    print("\n" + "=" * 60)
    print("4. Semantique des erreurs")
    print("=" * 60)
    cas = [
        ("port inexistant", 1, "zzzzz"),
        ("produit inexistant", 9, "wusrd"),
        ("sous-ressource inexistante", 1, "wusrd/zzz"),
        ("port connu, sous-ressource vide", 1, "wualm/"),
        ("casse differente", 1, "WUSRD"),
        ("nom tres long", 1, "a" * 64),
    ]
    out = {}
    for libelle, produit, port in cas:
        r = get(ip, produit, port, timeout=10)
        corps = r.get("body") or r.get("raw") or ""
        out[libelle] = {"status": r.get("status"), "error": r.get("error"),
                        "corps": str(corps)[:100], "ms": r["ms"]}
        print(f"  {libelle:<30} {r.get('status') or r.get('error')}  {str(corps)[:60]}")
        time.sleep(0.25)
    return out


def volet_cout_422(ip):
    print("\n" + "=" * 60)
    print("5. Cout d'un 422 — un balayage exhaustif est-il jouable ?")
    print("=" * 60)
    lat = []
    for i in range(10):
        r = get(ip, 1, f"zz{i:03d}", timeout=10)
        lat.append(r["ms"])
        time.sleep(0.1)
    med = statistics.median(lat)
    total = 26 ** 3
    heures = (total * (med + 100) / 1000) / 3600
    print(f"  mediane d'un 422 : {med:.0f} ms")
    print(f"  balayage exhaustif de wu+3 lettres ({total} noms) : ~{heures:.1f} h")
    return {"latences": lat, "mediane_ms": med, "estimation_heures": round(heures, 1)}


def volet_sub(ip):
    print("\n" + "=" * 60)
    print("6. Abonnements actifs (produit 0, port sub)")
    print("=" * 60)
    r = get(ip, 0, "sub", timeout=10)
    print(f"  {json.dumps(sans_secrets(r.get('body')))[:300] if r['ok'] else r.get('error')}")
    return {"ok": r["ok"], "body": sans_secrets(r.get("body"))}


def main():
    ip = discover()
    if not ip:
        print("AUCUN SOMNEO TROUVE", file=sys.stderr)
        return 1
    tout = {
        "pysomneo": volet_pysomneo(ip),
        "ports": volet_ports(ip),
        "index": volet_index(ip),
        "erreurs": volet_erreurs(ip),
        "cout_422": volet_cout_422(ip),
        "sub": volet_sub(ip),
    }
    nom = f"exploration-{time.strftime('%Y%m%dT%H%M%S')}.json"
    with open(nom, "w", encoding="utf-8") as fh:
        json.dump(tout, fh, ensure_ascii=False, indent=2)
    print(f"\nReleve -> {nom}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
