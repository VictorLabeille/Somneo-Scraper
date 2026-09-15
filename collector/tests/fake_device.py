"""Faux réveil HTTPS pour les tests hors appareil (plan §9).

Reproduit ce qui compte : les codes de réponse (`200`, `422`, `400`, `500`) ; **la concurrence**
— il compte les requêtes traitées en même temps, lectures et écritures, ce qui vérifie
l'invariant cardinal : sous n'importe quelle charge, la passerelle n'a jamais deux requêtes en
vol ; et, pour le relais (incrément 4), **les écritures telles que l'appareil les traite** :

- un `PUT` fusionne sa charge dans le port, pour que la relecture voie la valeur écrite ;
- `wungt` : `{"night": …}` ouvre ou ferme la session et la date ; `tg2bd`/`tendb` envoyés ne
  sont jamais pris, et une ouverture sur une session ouverte ne change rien (mesure P1) ;
- `wualm {"prfnr": n}` **sélectionne** un profil (1-16, 0 ignoré — P3), que `wualm/prfwu` rend
  alors ; `PUT wualm/prfwu` écrit le profil désigné par son `prfnr`, et `aenvs`/`aalms` suivent.

Quatre leviers pour les cas d'échec : `ignorer` (réponse `200`, rien d'appliqué — « acceptée
mais non reflétée »), `refuser` (code d'erreur sur `PUT`), `panne` (ce code sur toute requête,
lecture comprise — le réveil saturé ou parti) et `puts`, le journal des écritures.

N'écoute qu'en local, cert auto-signé (la passerelle appelle `pysomneo` avec `verify=False`).
Aucune valeur réelle : les corps sont des formes, pas des données de chambre. `wulgt` et `wudsk`
ont la forme du relevé `probes/results/sunset-777-*.json` ; `aenvs`, `aalms` et `files/*`, celle
que lit `pysomneo`.
"""
from __future__ import annotations

import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Corps par (produit, port). Forme réelle, valeurs neutres. Absent → 422 « No such Port ».
CORPS = {
    (1, "wusrd"): {"mslux": 0, "mstmp": 20, "msrhu": 50, "mssnd": 30,
                   "avlux": 0, "avtmp": 20, "avrhu": 50, "avsnd": 30},
    (1, "wusts"): {"wusts": 1, "brght": 1, "dspon": False},
    (1, "wungt"): {"night": False, "tg2bd": "", "tendb": "", "ntstr": "", "ntend": ""},
    (1, "wulgt"): {"ltlvl": 15, "ltlch": 0, "onoff": False, "ctype": 0, "tempy": True,
                   "ngtlt": False, "wucrv": [], "ltset": [], "pwmon": False, "pwmvs": [],
                   "diman": 0},
    (1, "wudsk"): {"durat": 30, "onoff": False, "curve": 20, "ctype": 0, "sndtp": 1,
                   "snddv": "off", "sndch": "", "sndlv": 12, "sndss": 200},
    (1, "wualm"): {"prfnr": 1, "snztm": 8},
    (1, "wuply"): {"onoff": False, "sdvol": 12, "snddv": "off", "sndch": ""},
    (1, "wutim"): {"yrltm": 2026, "moltm": 9, "dtltm": 13, "hrltm": 12, "miltm": 0, "scltm": 0},
    (1, "device"): {"serial": "TESTSERIAL", "type": "HF3671", "swversion": "1.2.3"},
    (1, "dataupload/temp.1/data"): {"svper": 898, "avtmp": 20.0, "lotmp": 19.9, "hitmp": 20.1},
    (1, "dataupload/hum.1/data"): {"svper": 898, "avhum": 50.0, "lohum": 49.0, "hihum": 51.0},
    (1, "dataupload/snd.1/data"): {"svper": 898, "avsnd": 30, "losnd": 26, "hisnd": 40,
                                   "absnd": [1, [0, 40, 100]], "rlsnd": [0]},
    (1, "dataupload/lux.1/data"): {"svper": 898, "avlux": 5.0, "lolux": 0.0, "hilux": 10.0,
                                   "ablux": [1, [0, 10, 100]], "rllux": [0]},
    (1, "files/wakeup"): {"1": {"name": "Wake A"}, "2": {"name": "Wake B"}},
    (1, "files/lightthemes"): {"0": {"name": "Light A"}, "1": {"name": "Light B"}},
    (1, "files/dusklightthemes"): {"0": {"name": "Dusk A"}, "1": {"name": "Dusk B"}},
    (1, "files/winddowndusk"): {"1": {"name": "Ambient A"}, "2": {"name": "Ambient B"}},
    (0, "time"): {"datetime": "2026-09-13T12:00:00+02:00", "dst": "+01:00"},
    (0, "backend"): {"dcs-state": "subscribed", "lastsignon": "2026-09-13T10:00:00Z"},
    (0, "transport"): {"state": "closed"},
    (0, "mem"): {"heap_size": 87200, "heap_free": 25000},
}


def _profil(n: int) -> dict:
    """Un profil d'alarme (`wualm/prfwu`, 20 champs). 1 : visible et armé ; 2 : visible,
    désarmé ; 3-16 : emplacements dormants, masqués et désarmés, comme sur l'appareil."""
    return {"prfnr": n, "prfvs": n <= 2, "prfen": n == 1, "pname": "",
            "ayear": 0, "amnth": 0, "alday": 0, "daynm": 254 if n == 1 else 62,
            "almhr": 7 if n == 1 else 8, "almmn": 0 if n == 1 else 30,
            "curve": 20, "durat": 30, "ctype": 0, "snddv": "wus", "sndch": "1", "sndlv": 12,
            "sndss": 0, "pwrsz": 0, "pszhr": 0, "pszmn": 0}


def _maintenant() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


class FakeDevice:
    """Serveur de test. `max_concurrent` : pic de requêtes traitées en parallèle."""

    def __init__(self, certfile: str, keyfile: str, delai_s: float = 0.03) -> None:
        self.max_concurrent = 0
        self.total = 0
        self._en_cours = 0
        self._verrou = threading.Lock()
        # copie profonde par instance : pas de fuite entre tests
        self.corps = {k: json.loads(json.dumps(v)) for k, v in CORPS.items()}
        self.profils = {n: _profil(n) for n in range(1, 17)}
        self.synchroniser()
        self.puts: list[tuple[tuple[int, str], dict]] = []
        self.ignorer: set[tuple[int, str]] = set()
        self.refuser: dict[tuple[int, str], int] = {}
        self.panne: int | None = None
        outer = self

        class H(BaseHTTPRequestHandler):
            def _cle(h) -> tuple[int, str] | None:
                # /di/v1/products/{produit}/{port...}
                parts = h.path.split("/di/v1/products/", 1)
                if len(parts) != 2:
                    return None
                produit_str, _, port = parts[1].partition("/")
                try:
                    return int(produit_str), port.rstrip("/")
                except ValueError:
                    return None

            def _compter(h, traiter) -> None:
                with outer._verrou:
                    outer._en_cours += 1
                    outer.total += 1
                    outer.max_concurrent = max(outer.max_concurrent, outer._en_cours)
                try:
                    time.sleep(delai_s)     # fenêtre pour révéler une concurrence éventuelle
                    traiter()
                finally:
                    with outer._verrou:
                        outer._en_cours -= 1

            def do_GET(h):
                h._compter(h._lire)

            def do_PUT(h):
                h._compter(h._ecrire)

            def _en_panne(h) -> bool:
                if outer.panne is None:
                    return False
                h._json(outer.panne, {"error": "Timeout"})
                return True

            def _lire(h):
                if h._en_panne():
                    return
                cle = h._cle()
                if cle is None:
                    return h._json(404, {"error": "Unknown product"})
                if cle[1] == "err400":
                    return h._json(400, {"error": "Bad Request"})
                if cle[1] == "err500":
                    return h._json(500, {"error": "Timeout"})
                corps = outer._corps(cle)
                if corps is None:
                    return h._json(422, {"error": "No such Port"})
                h._json(200, corps)

            def _ecrire(h):
                n = int(h.headers.get("Content-Length") or 0)
                try:
                    charge = json.loads(h.rfile.read(n) or b"{}")
                except ValueError:
                    charge = {}
                if h._en_panne():
                    return
                cle = h._cle()
                if cle is None:
                    return h._json(404, {"error": "Unknown product"})
                outer.puts.append((cle, charge))
                if cle in outer.refuser:
                    return h._json(outer.refuser[cle], {"error": "Refused"})
                if outer._corps(cle) is None:
                    return h._json(422, {"error": "No such Port"})
                if cle not in outer.ignorer and isinstance(charge, dict):
                    with outer._verrou:
                        outer._appliquer(cle, dict(charge))
                h._json(200, {})

            def _json(h, code, obj):
                b = json.dumps(obj).encode()
                h.send_response(code)
                h.send_header("Content-Type", "application/json")
                h.send_header("Content-Length", str(len(b)))
                h.end_headers()
                h.wfile.write(b)

            def log_message(h, *a):
                pass

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile, keyfile)
        self._srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self._srv.socket = ctx.wrap_socket(self._srv.socket, server_side=True)
        self._th = threading.Thread(target=self._srv.serve_forever, daemon=True)

    def _corps(self, cle: tuple[int, str]) -> dict | None:
        if cle == (1, "wualm/prfwu"):                  # le profil sélectionné
            return self.profils[self.corps[(1, "wualm")]["prfnr"]]
        return self.corps.get(cle)

    def _appliquer(self, cle: tuple[int, str], charge: dict) -> None:
        port = cle[1]
        if port == "wungt":
            cur = self.corps[cle]
            if "night" in charge:
                if charge["night"] and not cur["night"]:
                    cur.update(night=True, tg2bd=_maintenant())
                elif not charge["night"] and cur["night"]:
                    cur.update(night=False, tendb=_maintenant())
            return                                     # tg2bd/tendb envoyés : jamais pris (P1)
        if port == "wualm/prfwu":
            n = charge.get("prfnr")
            if isinstance(n, int) and 1 <= n <= 16:
                self.profils[n].update(charge)
                self.synchroniser()
            return
        if port == "wualm" and "prfnr" in charge:
            n = charge.pop("prfnr")
            if isinstance(n, int) and 1 <= n <= 16:    # 0 : accepté et ignoré (P3)
                self.corps[cle]["prfnr"] = n
        self.corps[cle] = {**self.corps[cle], **charge}

    def synchroniser(self) -> None:
        """Recalcule `aenvs`/`aalms` depuis les profils, comme l'appareil les tient."""
        p = [self.profils[n] for n in range(1, 17)]
        self.corps[(1, "wualm/aenvs")] = {
            "prfen": [x["prfen"] for x in p], "prfvs": [x["prfvs"] for x in p],
            "pwrsv": [v for x in p for v in (x["pwrsz"], x["pszhr"], x["pszmn"])]}
        self.corps[(1, "wualm/aalms")] = {
            "almhr": [x["almhr"] for x in p], "almmn": [x["almmn"] for x in p],
            "daynm": [x["daynm"] for x in p]}

    @property
    def host(self) -> str:
        ip, port = self._srv.server_address
        return f"{ip}:{port}"

    def __enter__(self) -> "FakeDevice":
        self._th.start()
        return self

    def __exit__(self, *exc) -> None:
        self._srv.shutdown()
        self._srv.server_close()
