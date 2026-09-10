"""`2` suit-il l'extinction de la veilleuse ? — sonde de refutation, 2026-09-10.

`veilleuse.py` (2026-09-09) n'a eteint la veilleuse seule qu'UNE fois, et n'a pas vu `2` a
~3 s — la ou la lampe le montrait au meme delai. Une affirmation publiee sur un essai unique :
cette sonde cherche ce qui la ferait tomber.

Chaque essai part d'un etat verifie (repos, bit de contexte revenu), relit `wulgt` apres
l'allumage, puis lit `wusts` en continu pendant 12 s apres l'extinction (~0,8 s entre deux
lectures : une poignee de main TLS par lecture). La fenetre de `2` va de t+0,7 s a t+8,2 s ;
un essai n'est retenu que s'il y compte au moins cinq lectures valides.

  S1 socle      veilleuse seule x5, alternee avec la lampe (niveau 3) x5. La lampe est le
                temoin positif : si elle ne montre pas `2`, la seance ne vaut rien.
  S2 duree      veilleuse tenue 15 s x2, puis 120 s x2 avant extinction — les extinctions
                du soir suivent plusieurs minutes d'allumage, l'essai du 09 moins de 2 s.
  S3 contexte   veilleuse allumee pendant le `2` de la lampe (258), tenue 12 s, eteinte x3.
  S4 ecriture   extinction par `{"ngtlt": false}` seul x3 — S1 ecrit les trois champs.

`--manuel N` : aucune ecriture. Lit `wulgt` et `wusts` en alternance pendant N secondes, pour
une extinction faite a la main sur l'appareil.

Le releve s'ecrit AU FIL DE L'EAU (JSONL, fsync) : un arret ne perd que l'essai en cours.
SIGTERM est gere : la sonde finit toujours par la restauration.

ECRITURE. `wulgt` uniquement — jamais `wualm`, jamais `fac`. Lumiere dans la chambre, le soir :
derogation accordee par Victor le 2026-09-10 pour cette mesure. La restauration est reessayee
jusqu'a relecture conforme — le WiFi de la carte a decroche douze fois ce soir-la.

Arrete `capture.py` au demarrage (SIGTERM par PID, lu dans /proc — jamais par motif) ; le
superviseur la voit comme sonde en cours, n'y touche pas, et relance la capture a la fin.
"""
import json
import os
import signal
import ssl
import sys
import time
import urllib.error
import urllib.request

from somneo_probe import discover, get

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

PAUSE = 0.2
FENETRE = (0.7, 8.2)     # bornes de `2` mesurees le 2026-09-09
DUREE_TRACE = 12.0
REPOS_S = 35             # le bit 0 revient 15 a 30 s apres la derniere lumiere
MIN_LECTURES = 5
NIVEAU_LAMPE = 3

_stop = False


class Arret(Exception):
    pass


def _arret(signum, frame):
    global _stop
    _stop = True


def verifier():
    if _stop:
        raise Arret()


def dormir(s):
    fin = time.monotonic() + s
    while True:
        verifier()
        reste = fin - time.monotonic()
        if reste <= 0:
            return
        time.sleep(min(0.5, reste))


class Releve:
    """JSONL en ajout, vide a chaque ligne."""

    def __init__(self, nom):
        self.nom = nom
        self.fh = open(nom, "a", encoding="utf-8")

    def ecrire(self, **rec):
        rec["ts"] = time.time()
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())


def put(ip, charge, pause=PAUSE):
    url = f"https://{ip}/di/v1/products/1/wulgt"
    rec = {"charge": charge, "ok": False}
    try:
        req = urllib.request.Request(url, data=json.dumps(charge).encode(), method="PUT",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15, context=CTX) as r:
            rec.update(status=r.status, ok=True)
    except urllib.error.HTTPError as exc:
        rec.update(status=exc.code, error=f"HTTP {exc.code}")
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"
    rec["t_fin"] = time.monotonic()
    if pause:
        time.sleep(pause)
    return rec


def lire(ip, port):
    r = get(ip, 1, port)
    return r.get("body") if r.get("ok") else None


def etat(ip):
    lgt = lire(ip, "wulgt") or {}
    time.sleep(PAUSE)
    sts = lire(ip, "wusts") or {}
    time.sleep(PAUSE)
    return {"wusts": sts.get("wusts"), "onoff": lgt.get("onoff"), "ngtlt": lgt.get("ngtlt"),
            "ltlvl": lgt.get("ltlvl")}


def arreter_capture():
    """SIGTERM a capture.py par PID. `pkill -f` se tuerait lui-meme depuis SSH."""
    pids = []
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/cmdline", "rb") as fh:
                args = fh.read().split(b"\0")
        except OSError:
            continue
        if len(args) >= 2 and args[0].endswith(b"python3") and args[1] == b"capture.py":
            pids.append(int(p))
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    fin = time.monotonic() + 40
    while time.monotonic() < fin and any(os.path.exists(f"/proc/{pid}") for pid in pids):
        time.sleep(0.5)
    vivants = [pid for pid in pids if os.path.exists(f"/proc/{pid}")]
    return pids, vivants


def trouver():
    for _ in range(10):
        try:
            ip = discover()
        except OSError:
            ip = None
        if ip:
            return ip
        time.sleep(10)
    return None


def tracer(ip, t0, duree):
    """Lit `wusts` en continu pendant `duree` secondes a partir de `t0` (monotonic)."""
    trace = []
    while time.monotonic() - t0 < duree:
        verifier()
        t = time.monotonic() - t0
        r = get(ip, 1, "wusts")
        v = (r.get("body") or {}).get("wusts") if r.get("ok") else None
        trace.append({"t": round(t, 2), "ms": r.get("ms"), "wusts": v})
        time.sleep(PAUSE)
    return trace


def juger(trace):
    dans = [s for s in trace if FENETRE[0] <= s["t"] <= FENETRE[1] and s["wusts"] is not None]
    return {"lectures_fenetre": len(dans), "valide": len(dans) >= MIN_LECTURES,
            "vu_2": any(s["wusts"] == 2 for s in trace),
            "suite": [s["wusts"] for s in trace]}


def repos(ip, ltlvl, attente=REPOS_S):
    """Tout eteint, bit de contexte revenu. Deux tentatives, puis l'essai est ecarte."""
    for _ in range(2):
        put(ip, {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
        dormir(attente)
        e = etat(ip)
        if e["wusts"] == 1 and e["onoff"] is False and e["ngtlt"] is False:
            return e, True
    return e, False


def essai(ip, rel, serie, n, lumiere, ltlvl, tenue=2.0, extinction="complete",
          contexte="repos"):
    depart, conforme = repos(ip, ltlvl)
    rec = {"serie": serie, "n": n, "lumiere": lumiere, "tenue_s": tenue,
           "extinction": extinction, "contexte": contexte, "depart": depart,
           "depart_conforme": conforme}
    if not conforme:
        rel.ecrire(type="essai", ecarte="depart non conforme", **rec)
        return rec

    if contexte == "transitoire":
        # La lampe laisse `2` derriere elle ; la veilleuse s'allume dedans (258 attendu).
        put(ip, {"onoff": True, "ltlvl": NIVEAU_LAMPE})
        dormir(1.5)
        put(ip, {"onoff": False})
        dormir(2.0)
        rec["avant_allumage"] = lire(ip, "wusts")
        time.sleep(PAUSE)

    if lumiere == "veilleuse":
        allumage = put(ip, {"ngtlt": True})
    else:
        allumage = put(ip, {"onoff": True, "ltlvl": NIVEAU_LAMPE})
    t_allume = allumage["t_fin"]
    dormir(1.0)
    allume = etat(ip)
    rec["allumage_ok"] = allumage["ok"]
    rec["allume"] = allume
    rec["allumage_confirme"] = (allume["ngtlt"] is True if lumiere == "veilleuse"
                                else allume["onoff"] is True)
    # Pendant la tenue, `wusts` est echantillonne sans hate : c'est la suite de l'etat allume.
    tenue_trace = []
    while time.monotonic() - t_allume < tenue:
        verifier()
        v = (lire(ip, "wusts") or {}).get("wusts")
        tenue_trace.append({"t": round(time.monotonic() - t_allume, 1), "wusts": v})
        dormir(min(2.0, max(0.0, tenue - (time.monotonic() - t_allume))))
    rec["tenue_trace"] = tenue_trace

    charge = ({"ngtlt": False} if extinction == "ngtlt_seul"
              else {"onoff": False, "ngtlt": False, "ltlvl": ltlvl})
    ext = put(ip, charge, pause=0)
    rec["extinction_ok"] = ext["ok"]
    trace = tracer(ip, ext["t_fin"], DUREE_TRACE)
    rec["trace"] = trace
    rec["apres"] = etat(ip)
    rec.update(juger(trace))
    rel.ecrire(type="essai", **rec)
    print(f"{serie} #{n} {lumiere:<9} tenue={tenue:>5}s ext={extinction:<10} ctx={contexte:<11} "
          f"allume={allume['wusts']} -> {rec['suite']}  2={'OUI' if rec['vu_2'] else 'non'}"
          f"{'' if rec['valide'] else '  (INVALIDE)'}", flush=True)
    return rec


def restaurer(ip, rel, initial):
    """Reessaie jusqu'a relecture conforme, dix minutes au plus."""
    cible = {"onoff": bool(initial["onoff"]), "ngtlt": bool(initial["ngtlt"]),
             "ltlvl": initial["ltlvl"] or 15}
    fin = time.monotonic() + 600
    while True:
        put(ip, cible)
        time.sleep(2.5)
        e = etat(ip)
        ok = (e["onoff"] == cible["onoff"] and e["ngtlt"] == cible["ngtlt"]
              and e["ltlvl"] == cible["ltlvl"])
        if ok or time.monotonic() > fin:
            rel.ecrire(type="restauration", cible=cible, etat=e, conforme=ok)
            print(f"restauration : {'conforme' if ok else 'NON CONFORME — A VERIFIER'} {e}",
                  flush=True)
            return ok
        time.sleep(10)


def manuel(ip, rel, duree):
    t0 = time.monotonic()
    while time.monotonic() - t0 < duree:
        verifier()
        for port in ("wulgt", "wusts"):
            r = get(ip, 1, port)
            b = r.get("body") if r.get("ok") else None
            rel.ecrire(type="manuel", t=round(time.monotonic() - t0, 2), port=port,
                       ok=r.get("ok"),
                       valeur=None if b is None else
                       ({"wusts": b.get("wusts")} if port == "wusts"
                        else {"onoff": b.get("onoff"), "ngtlt": b.get("ngtlt")}))
            time.sleep(PAUSE)


def main():
    global _stop
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    mode_manuel = "--manuel" in sys.argv
    duree_manuel = int(sys.argv[sys.argv.index("--manuel") + 1]) if mode_manuel else 0

    rel = Releve(f"extinction-veilleuse-{'manuel-' if mode_manuel else ''}"
                 f"{time.strftime('%Y%m%dT%H%M%S')}.jsonl")
    pids, vivants = arreter_capture()
    rel.ecrire(type="debut", capture_arretee=pids, capture_vivante=vivants,
               mode="manuel" if mode_manuel else "auto")
    if vivants:
        print("capture toujours vivante : abandon, deux clients tomberaient ensemble",
              file=sys.stderr)
        rel.ecrire(type="fin", abandon="capture vivante")
        return 1
    ip = trouver()
    if not ip:
        rel.ecrire(type="fin", abandon="Somneo introuvable")
        return 1

    initial = etat(ip)
    rel.ecrire(type="initial", **initial)
    print(f"etat initial : {initial}", flush=True)
    if initial["ltlvl"] is None:
        rel.ecrire(type="fin", abandon="wulgt illisible")
        return 1

    if mode_manuel:
        try:
            manuel(ip, rel, duree_manuel)
        except Arret:
            pass
        rel.ecrire(type="fin")
        return 0

    ltlvl = initial["ltlvl"]
    try:
        n = 0
        for i in range(5):                                    # S1 socle, alterne
            essai(ip, rel, "S1", i + 1, "veilleuse", ltlvl)
            essai(ip, rel, "S1", i + 1, "lampe", ltlvl)
        for tenue in (15, 15, 120, 120):                      # S2 duree
            n += 1
            essai(ip, rel, "S2", n, "veilleuse", ltlvl, tenue=tenue)
        for i in range(3):                                    # S3 contexte
            essai(ip, rel, "S3", i + 1, "veilleuse", ltlvl, tenue=12, contexte="transitoire")
        for i in range(3):                                    # S4 ecriture
            essai(ip, rel, "S4", i + 1, "veilleuse", ltlvl, extinction="ngtlt_seul")
    except Arret:
        rel.ecrire(type="interruption")
        print("interrompue — restauration", flush=True)
    finally:
        _stop = False
        restaurer(ip, rel, initial)
        rel.ecrire(type="fin")
    print(f"Releve -> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
