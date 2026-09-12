"""Socle des sondes qui ecrivent — une connexion reutilisee, des instantanes, une restauration.

`somneo_probe.py` ouvre une connexion neuve a chaque requete (~500 ms, docs/somneo-api.md §7) :
bien pour lire, trop lent pour dater une ecriture a la seconde. `Session` garde UNE connexion
TLS ouverte (~36 ms par requete) et n'en ouvre jamais deux : l'appareil n'en sert qu'une, et
la seconde fait tomber celle qui etait en vol.

Ce module n'ecrit rien de lui-meme : il fournit `Session.put`, c'est la sonde qui decide.
Bibliotheque standard uniquement, comme les autres sondes. `somneo_probe.py` n'est pas modifie :
`capture.py` l'importe a chaque relance.

Essais hors appareil : `--hote 127.0.0.1:8443` vise un faux reveil, et la variable
SONDE_ACCELERATION divise toutes les attentes. Elle vaut 1 par defaut et ne sert jamais sur
l'appareil.
"""
import http.client
import json
import os
import signal
import ssl
import time
from datetime import datetime

from somneo_probe import discover

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

ESPACEMENT = 0.2        # s entre la fin d'une requete et l'envoi de la suivante
ACCELERATION = float(os.environ.get("SONDE_ACCELERATION", "1"))

# Relus avant et apres chaque ecriture. Les capteurs (wusrd, dataupload) n'y sont pas : ils
# bougent seuls. Les horloges y sont, pour la datation, mais hors du diff. Les alarmes se
# relisent port par port : la racine `wualm` ne renvoie que des sous-objets vides.
INSTANTANE = [(1, "wusts"), (1, "wungt"), (1, "wulgt"), (1, "wudsk"), (1, "wualm"),
              (1, "wualm/aenvs"), (1, "wualm/aalms"), (1, "wualm/alctr"), (1, "wualm/prfwu"),
              (1, "wuply"), (1, "wutms"), (1, "wutim"), (0, "time"), (0, "backend"),
              (0, "transport")]
HORLOGES = ("1/wutim", "0/time")
VOLATILS = ("0/backend.lastsignon",)     # la liaison cloud se reconnecte seule

# Bits 2 (alarme), 4 (rappel) et 11 (appareil actif) de `wusts` : aucune ecriture.
BITS_ALARME = (1 << 2) | (1 << 4) | (1 << 11)


class Arret(Exception):
    """Levee par `verifier()` apres SIGTERM ou SIGINT : la sonde passe a sa restauration."""


class Inattendu(Exception):
    """Un effet que le protocole ne prevoit pas : on cesse d'ecrire, on restaure.

    Continuer apres un effet de bord, ce serait le repeter sur un appareil en service."""


_arret = False


def _signal(signum, frame):
    global _arret
    _arret = True


def installer_signaux():
    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)


def verifier():
    if _arret:
        raise Arret()


def lever_arret():
    """Avant la restauration : elle doit pouvoir attendre et relire, meme apres un SIGTERM."""
    global _arret
    _arret = False


def dormir(secondes, interruptible=True):
    """Attend `secondes` / ACCELERATION. Interruptible : leve Arret au signal."""
    fin = time.monotonic() + secondes / ACCELERATION
    while True:
        if interruptible:
            verifier()
        reste = fin - time.monotonic()
        if reste <= 0:
            return
        time.sleep(min(0.5, reste))


def attendre_depuis(t_ref, secondes):
    """Attend que `secondes` (a l'echelle de l'appareil) se soient ecoulees depuis `t_ref`."""
    ecoule = (time.monotonic() - t_ref) * ACCELERATION
    if secondes > ecoule:
        dormir(secondes - ecoule)


class Session:
    """Une seule connexion HTTPS vers le reveil, reutilisee, jamais doublee."""

    def __init__(self, hote, timeout=15):
        self.hote = hote
        self.timeout = timeout
        self.espacement = ESPACEMENT   # abaisse par la mesure de phase (P2), jamais a zero
        self._conn = None
        self._fin = 0.0

    def fermer(self):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _requete(self, methode, produit, port, charge):
        attente = self._fin + self.espacement / ACCELERATION - time.monotonic()
        if attente > 0:
            time.sleep(attente)
        if self._conn is None:
            self._conn = http.client.HTTPSConnection(self.hote, timeout=self.timeout,
                                                     context=CTX)
        entetes = {"Connection": "keep-alive"}
        corps = None
        if charge is not None:
            corps = json.dumps(charge).encode()
            entetes["Content-Type"] = "application/json"
        rec = {"methode": methode, "produit": produit, "port": port, "ok": False}
        if charge is not None:
            rec["charge"] = charge
        t0 = time.monotonic()
        rec["t_envoi"] = time.time()
        try:
            self._conn.request(methode, f"/di/v1/products/{produit}/{port}", body=corps,
                               headers=entetes)
            rep = self._conn.getresponse()
            brut = rep.read().decode("utf-8", errors="replace")
            rec["status"] = rep.status
            try:
                rec["body"] = json.loads(brut) if brut else None
            except ValueError:
                rec["raw"] = brut
            rec["ok"] = 200 <= rep.status < 300
            if not rec["ok"]:
                rec["error"] = f"HTTP {rep.status}"
        except (http.client.HTTPException, OSError) as exc:
            self.fermer()
            rec["error"] = f"{type(exc).__name__}: {exc}"
        rec["t_recu"] = time.time()
        rec["ms"] = round((time.monotonic() - t0) * 1000, 1)
        self._fin = time.monotonic()
        return rec

    def get(self, produit, port):
        """GET, avec une seconde tentative sur erreur de transport : une lecture se rejoue."""
        rec = self._requete("GET", produit, port, None)
        if not rec["ok"] and "status" not in rec:
            rec = self._requete("GET", produit, port, None)
            rec["seconde_tentative"] = True
        return rec

    def put(self, produit, port, charge):
        """PUT, UNE seule tentative.

        Rejouer une ecriture dont on ne sait pas si elle est passee fausserait precisement ce
        qu'on mesure (P1-2 : un second `night: true` deplace-t-il l'heure ?). C'est la
        relecture qui dit ce qui s'est passe. Les sondes lisent toujours juste avant d'ecrire :
        une connexion morte est remplacee par ce GET, pas par le PUT."""
        return self._requete("PUT", produit, port, charge)

    def corps(self, produit, port):
        rec = self.get(produit, port)
        return rec.get("body") if rec["ok"] else None


def lire_heure(valeur):
    """ISO 8601 avec decalage -> datetime, ou None (champ vide, format inattendu)."""
    try:
        return datetime.fromisoformat(valeur)
    except (TypeError, ValueError):
        return None


def iso(dt):
    return dt.isoformat() if dt is not None else None


def heure_appareil(s):
    """L'heure du reveil (port `time`, a la seconde) et la requete qui l'a lue."""
    rec = s.get(0, "time")
    corps = rec.get("body") if rec["ok"] else None
    return (lire_heure(corps.get("datetime")) if isinstance(corps, dict) else None), rec


def alarme_en_cours(valeur_wusts):
    """Vrai si une alarme sonne, est en rappel, ou si `wusts` est illisible (dans le doute)."""
    return not isinstance(valeur_wusts, int) or bool(valeur_wusts & BITS_ALARME)


def instantane(s, interruptible=True):
    """Relit les ports d'INSTANTANE. Un port illisible est note, pas omis."""
    etat = {}
    for produit, port in INSTANTANE:
        if interruptible:
            verifier()
        rec = s.get(produit, port)
        etat[f"{produit}/{port}"] = rec["body"] if rec["ok"] else {"_erreur": rec.get("error")}
    return etat


def aplatir(valeur, prefixe=""):
    if isinstance(valeur, dict):
        plat = {}
        for cle, sous in valeur.items():
            plat.update(aplatir(sous, f"{prefixe}.{cle}" if prefixe else str(cle)))
        return plat
    return {prefixe: valeur}


_ABSENT = object()


def difference(avant, apres, ignorer=HORLOGES):
    """Champs qui different entre deux instantanes : [[chemin, avant, apres], ...].

    Les listes se comparent entieres. `ignorer` retire des ports entiers (les horloges bougent
    seules). Un champ absent d'un cote est une difference."""
    a = {k: v for k, v in aplatir(avant).items() if k.split(".")[0] not in ignorer}
    b = {k: v for k, v in aplatir(apres).items() if k.split(".")[0] not in ignorer}
    return [[k, a.get(k), b.get(k)] for k in sorted(set(a) | set(b))
            if a.get(k, _ABSENT) != b.get(k, _ABSENT)]


def effets_de_bord(avant, apres, ports_ecrits):
    """Ce qui a change hors des ports ecrits, des horloges et des champs volatils connus."""
    return [d for d in difference(avant, apres)
            if d[0].split(".")[0] not in ports_ecrits and d[0] not in VOLATILS]


def champs_changes(a, b):
    """Cles de premier niveau dont la valeur differe entre deux corps d'un meme port."""
    a, b = a or {}, b or {}
    return sorted(k for k in set(a) | set(b) if a.get(k, _ABSENT) != b.get(k, _ABSENT))


class Journal:
    """JSONL en ajout, vide a chaque ligne : un arret ne perd que l'essai en cours."""

    def __init__(self, nom):
        self.nom = nom
        self._fh = open(nom, "a", encoding="utf-8")

    def ecrire(self, **rec):
        rec["ts"] = time.time()
        self._fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        self._fh.flush()
        os.fsync(self._fh.fileno())

    def fermer(self):
        self._fh.close()


def arreter_capture():
    """SIGTERM a capture.py par PID, lu dans /proc. `pkill -f` se tuerait lui-meme via SSH."""
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


def trouver(hote_force=None):
    """L'adresse du reveil par SSDP, dix essais espaces de 10 s. `hote_force` court-circuite."""
    if hote_force:
        return hote_force
    for _ in range(10):
        try:
            ip = discover()
        except OSError:          # carte hors reseau : discover() leve au lieu de rendre None
            ip = None
        if ip:
            return ip
        time.sleep(10)
    return None
