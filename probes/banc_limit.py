"""Le correctif de #8 execute tel qu'il sera propose — `TCPConnector(ssl=False, limit=1)`.

Le 2026-09-07, `async_pool.py` a mesure `limit=1` en REMPLACANT `_get_session` : une injection,
pas le fichier que la PR modifiera. Ici, deux paquets cote a cote sur la carte :

  asyncpkg/      la branche `ai-improvements` telle qu'en amont (365d313)
  asyncpkg_fix/  la meme, `api.py` patche d'une ligne — le diff exact de la PR

Ce qui pourrait faire tomber le correctif, et que chaque volet cherche :

  T1  file profonde — N `fetch_data` en `gather`, N = 1, 2, 3, 6, 12, 24. aiohttp compte
      l'attente d'une place dans le pool dans `timeout.connect` (5 s ici) : a partir d'une
      certaine profondeur, une requete pourrait expirer EN ATTENDANT. On compte aussi les
      reessais silencieux (journal DEBUG de `pysomneo.api`) : un succes obtenu a la troisieme
      tentative est une panne masquee — c'est tout le sujet de #8.
  T2  motif Home Assistant — une requete isolee lancee au hasard pendant un `fetch_data`.
  T3  session recreee apres `_reset_session()` — garde-t-elle `limit=1` ?
  T4  connexion reutilisee apres 30 s et 90 s d'inactivite.
  T5  deux instances `Somneo` dans le meme processus — `limit` vaut par session.

Lecture seule : que des GET (`fetch_data` n'ecrit rien). Arrete `capture.py` au demarrage par
son PID ; le superviseur la relance a la fin. Releve JSONL ecrit au fil de l'eau.
A lancer avec `.venv/bin/python3` : `aiohttp` n'est que la.
"""
import asyncio
import inspect
import json
import logging
import os
import random
import signal
import sys
import time

sys.path.insert(0, ".")
from somneo_probe import discover

PAQUETS = ("asyncpkg", "asyncpkg_fix")
PALIERS = (1, 2, 3, 6, 12, 24)
SERIES = 3
ESSAIS_HA = 15
PLAFOND_S = 240

_stop = False


def _arret(signum, frame):
    global _stop
    _stop = True


class Releve:
    def __init__(self, nom):
        self.nom = nom
        self.fh = open(nom, "a", encoding="utf-8")

    def ecrire(self, **rec):
        rec["ts"] = time.time()
        self.fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        self.fh.flush()
        os.fsync(self.fh.fileno())


class Reessais(logging.Handler):
    """Compte les tentatives echouees que la boucle de `SomneoSession.request` absorbe."""

    def __init__(self):
        super().__init__(logging.DEBUG)
        self.remise_a_zero()

    def remise_a_zero(self):
        self.par_type = {}
        self.abandons = 0
        self.resets = 0

    def emit(self, record):
        msg = record.msg if isinstance(record.msg, str) else ""
        if msg.startswith("%s (attempt") and record.args:
            t = record.args[0]
            self.par_type[t] = self.par_type.get(t, 0) + 1
        elif msg.startswith("All %d attempts failed"):
            self.abandons += 1
        elif msg.startswith("Resetting session"):
            self.resets += 1

    def bilan(self):
        return {"echecs_absorbes": dict(self.par_type), "abandons": self.abandons,
                "resets": self.resets}


REESSAIS = Reessais()
_log = logging.getLogger("pysomneo.api")
_log.setLevel(logging.DEBUG)
_log.addHandler(REESSAIS)
_log.propagate = False


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
    return pids, [pid for pid in pids if os.path.exists(f"/proc/{pid}")]


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


def charger(paquet):
    """Importe `pysomneo` depuis `paquet`, en purgeant l'autre. Verifie ce qui est charge."""
    for m in [m for m in sys.modules if m == "pysomneo" or m.startswith("pysomneo.")]:
        del sys.modules[m]
    sys.path[:] = [p for p in sys.path if p not in PAQUETS]
    sys.path.insert(0, paquet)
    import pysomneo
    import pysomneo.api as api
    chemin = pysomneo.__file__ or ""
    if f"/{paquet}/" not in chemin:
        raise RuntimeError(f"mauvais paquet charge : {chemin}")
    corrige = "limit=1" in inspect.getsource(api.SomneoSession._get_session)
    if corrige != (paquet == "asyncpkg_fix"):
        raise RuntimeError(f"{paquet} : correctif {'present' if corrige else 'absent'} a tort")
    return pysomneo, api


async def fermer(s):
    try:
        await s._client.session.close()
    except Exception:
        pass


async def chrono(coro):
    t = time.monotonic()
    try:
        await coro
        return {"ok": True, "s": round(time.monotonic() - t, 2)}
    except Exception as exc:
        cause = exc.__cause__
        return {"ok": False, "s": round(time.monotonic() - t, 2), "err": type(exc).__name__,
                "cause": type(cause).__name__ if cause else None}


def limite_session(s):
    sess = s._client.session._session
    if sess is None or sess.closed:
        return None
    return sess.connector.limit


async def t1(ip, rel, paquet, n, serie):
    pysomneo, _ = charger(paquet)
    s = pysomneo.Somneo(ip)
    await chrono(s.fetch_data(force_slow_refresh=True))            # amorce, hors mesure
    await asyncio.sleep(1)
    REESSAIS.remise_a_zero()
    t0 = time.monotonic()
    try:
        appels = await asyncio.wait_for(asyncio.gather(
            *[chrono(s.fetch_data(force_slow_refresh=True)) for _ in range(n)]), PLAFOND_S)
    except asyncio.TimeoutError:
        appels = [{"ok": False, "err": "plafond"}] * n
    duree = round(time.monotonic() - t0, 2)
    rec = {"volet": "T1", "paquet": paquet, "taches": n, "serie": serie, "duree_s": duree,
           "ok": sum(a["ok"] for a in appels), "total": n,
           "pire_s": max((a.get("s") or 0) for a in appels),
           "erreurs": [a for a in appels if not a["ok"]], "limite": limite_session(s),
           **REESSAIS.bilan()}
    await fermer(s)
    rel.ecrire(**rec)
    print(f"T1 {paquet:<12} N={n:<2} s{serie} {rec['ok']}/{n}  {duree:6.1f} s  pire "
          f"{rec['pire_s']:5.1f} s  absorbes={rec['echecs_absorbes'] or 0} "
          f"abandons={rec['abandons']}", flush=True)


async def t2(ip, rel, paquet, essai):
    pysomneo, _ = charger(paquet)
    s = pysomneo.Somneo(ip)
    await chrono(s.fetch_data(force_slow_refresh=True))
    await asyncio.sleep(1)
    REESSAIS.remise_a_zero()
    retard = random.uniform(0.05, 0.4)

    async def isolee():
        await asyncio.sleep(retard)
        return await chrono(s._client.get_sensor_data())

    rafraichi, action = await asyncio.gather(chrono(s.fetch_data(force_slow_refresh=True)),
                                             isolee())
    rec = {"volet": "T2", "paquet": paquet, "essai": essai, "retard_s": round(retard, 3),
           "rafraichissement": rafraichi, "action": action, **REESSAIS.bilan()}
    await fermer(s)
    rel.ecrire(**rec)
    print(f"T2 {paquet:<12} #{essai:<2} refresh {'ok' if rafraichi['ok'] else 'ECHEC'} "
          f"{rafraichi['s']:5.2f} s  action {'ok' if action['ok'] else 'ECHEC'} "
          f"{action['s']:5.2f} s  absorbes={rec['echecs_absorbes'] or 0}", flush=True)


async def t3(rel, paquet):
    _, api = charger(paquet)
    sess = api.SomneoSession(base_url="https://127.0.0.1/")
    avant = (await sess._get_session()).connector.limit
    await sess._reset_session()
    apres = (await sess._get_session()).connector.limit
    await sess.close()
    rel.ecrire(volet="T3", paquet=paquet, limite_avant_reset=avant, limite_apres_reset=apres)
    print(f"T3 {paquet:<12} limit avant reset={avant}  apres reset={apres}", flush=True)


async def t4(ip, rel, paquet):
    pysomneo, _ = charger(paquet)
    s = pysomneo.Somneo(ip)
    await chrono(s.fetch_data(force_slow_refresh=True))
    for repos in (30, 90):
        await asyncio.sleep(repos)
        REESSAIS.remise_a_zero()
        r = await chrono(s.fetch_data(force_slow_refresh=True))
        rel.ecrire(volet="T4", paquet=paquet, repos_s=repos, appel=r, **REESSAIS.bilan())
        print(f"T4 {paquet:<12} apres {repos} s : {'ok' if r['ok'] else 'ECHEC'} {r['s']:5.2f} s "
              f"absorbes={REESSAIS.bilan()['echecs_absorbes'] or 0}", flush=True)
    await fermer(s)


async def t5(ip, rel, paquet, serie):
    pysomneo, _ = charger(paquet)
    a, b = pysomneo.Somneo(ip), pysomneo.Somneo(ip)
    await chrono(a.fetch_data(force_slow_refresh=True))
    await chrono(b.fetch_data(force_slow_refresh=True))
    await asyncio.sleep(1)
    REESSAIS.remise_a_zero()
    ra, rb = await asyncio.gather(chrono(a.fetch_data(force_slow_refresh=True)),
                                  chrono(b.fetch_data(force_slow_refresh=True)))
    rel.ecrire(volet="T5", paquet=paquet, serie=serie, a=ra, b=rb, **REESSAIS.bilan())
    print(f"T5 {paquet:<12} s{serie} A {'ok' if ra['ok'] else 'ECHEC'} {ra['s']:5.2f} s  "
          f"B {'ok' if rb['ok'] else 'ECHEC'} {rb['s']:5.2f} s  "
          f"absorbes={REESSAIS.bilan()['echecs_absorbes'] or 0}", flush=True)
    await fermer(a)
    await fermer(b)


async def main():
    signal.signal(signal.SIGTERM, _arret)
    signal.signal(signal.SIGINT, _arret)
    rel = Releve(time.strftime("banc-limit-%Y%m%dT%H%M%S.jsonl"))
    pids, vivants = arreter_capture()
    rel.ecrire(volet="debut", capture_arretee=pids, capture_vivante=vivants)
    if vivants:
        rel.ecrire(volet="fin", abandon="capture vivante")
        return 1
    ip = trouver()
    if not ip:
        rel.ecrire(volet="fin", abandon="Somneo introuvable")
        return 1
    import aiohttp
    rel.ecrire(volet="config", aiohttp=aiohttp.__version__, paliers=PALIERS, series=SERIES)
    print(f"aiohttp {aiohttp.__version__}", flush=True)

    etapes = []
    for p in PAQUETS:
        etapes.append(lambda p=p: t3(rel, p))
    for n in PALIERS:
        for serie in range(1, SERIES + 1):
            ordre = PAQUETS if serie % 2 else PAQUETS[::-1]    # l'ordre alterne d'une serie a l'autre
            for p in ordre:
                etapes.append(lambda p=p, n=n, serie=serie: t1(ip, rel, p, n, serie))
    for essai in range(1, ESSAIS_HA + 1):
        for p in (PAQUETS if essai % 2 else PAQUETS[::-1]):
            etapes.append(lambda p=p, essai=essai: t2(ip, rel, p, essai))
    for p in PAQUETS:
        etapes.append(lambda p=p: t4(ip, rel, p))
    for serie in range(1, 6):
        for p in PAQUETS:
            etapes.append(lambda p=p, serie=serie: t5(ip, rel, p, serie))

    try:
        for etape in etapes:
            if _stop:
                rel.ecrire(volet="interruption")
                break
            await etape()
            await asyncio.sleep(3)
    finally:
        rel.ecrire(volet="fin")
        print(f"Releve -> {rel.nom}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
