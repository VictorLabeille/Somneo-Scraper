"""Faux réveil HTTPS pour les tests hors appareil (plan §9).

Reproduit les deux comportements qui comptent : les codes de réponse (`200`, `422`, `400`,
`500`) et **la concurrence** — il compte le nombre de requêtes traitées en même temps, ce qui
permet de vérifier l'invariant cardinal : sous n'importe quelle charge, la passerelle n'a jamais
deux requêtes en vol.

N'écoute qu'en local, cert auto-signé (la passerelle appelle `pysomneo` avec `verify=False`).
Aucune valeur réelle : les corps sont des formes, pas des données de chambre.
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
    (1, "wulgt"): {"ltlvl": 15, "onoff": False, "ngtlt": False},
    (1, "wudsk"): {"durat": 30, "onoff": False, "curve": 20},
    (1, "wualm"): {"prfnr": 1, "snztm": 8},
    (1, "wualm/aenvs"): {"prfen": [True, False], "prfvs": [True, True]},
    (1, "wualm/aalms"): {"almhr": [7], "almmn": [0]},
    (1, "wuply"): {"onoff": False, "snddv": "off"},
    (1, "wutim"): {"yrltm": 2026, "moltm": 9, "dtltm": 13, "hrltm": 12, "miltm": 0, "scltm": 0},
    (1, "device"): {"serial": "TESTSERIAL", "type": "HF3671", "swversion": "1.2.3"},
    (1, "dataupload/temp.1/data"): {"svper": 898, "avtmp": 20.0, "lotmp": 19.9, "hitmp": 20.1},
    (1, "dataupload/hum.1/data"): {"svper": 898, "avhum": 50.0, "lohum": 49.0, "hihum": 51.0},
    (1, "dataupload/snd.1/data"): {"svper": 898, "avsnd": 30, "losnd": 26, "hisnd": 40,
                                   "absnd": [1, [0, 40, 100]], "rlsnd": [0]},
    (1, "dataupload/lux.1/data"): {"svper": 898, "avlux": 5.0, "lolux": 0.0, "hilux": 10.0,
                                   "ablux": [1, [0, 10, 100]], "rllux": [0]},
    (1, "files/wakeup"): {"1": "Sunny day"},
    (1, "files/lightthemes"): {"1": "Sunny day"},
    (1, "files/dusklightthemes"): {"1": "Soft Rain"},
    (1, "files/winddowndusk"): {"1": "Forest"},
    (0, "time"): {"datetime": "2026-09-13T12:00:00+02:00", "dst": "+01:00"},
    (0, "backend"): {"dcs-state": "subscribed", "lastsignon": "2026-09-13T10:00:00Z"},
    (0, "transport"): {"state": "closed"},
    (0, "mem"): {"heap_size": 87200, "heap_free": 25000},
}


class FakeDevice:
    """Serveur de test. `max_concurrent` : pic de requêtes traitées en parallèle."""

    def __init__(self, certfile: str, keyfile: str, delai_s: float = 0.03) -> None:
        self.max_concurrent = 0
        self.total = 0
        self._en_cours = 0
        self._verrou = threading.Lock()
        outer = self

        class H(BaseHTTPRequestHandler):
            def _repondre(h):
                # /di/v1/products/{produit}/{port...}
                parts = h.path.split("/di/v1/products/", 1)
                if len(parts) != 2:
                    return h._json(404, {"error": "Unknown product"})
                reste = parts[1]
                produit_str, _, port = reste.partition("/")
                port = port.rstrip("/")
                try:
                    produit = int(produit_str)
                except ValueError:
                    return h._json(404, {"error": "Unknown product"})
                if port == "err400":
                    return h._json(400, {"error": "Bad Request"})
                if port == "err500":
                    return h._json(500, {"error": "Timeout"})
                corps = CORPS.get((produit, port))
                if corps is None:
                    return h._json(422, {"error": "No such Port"})
                h._json(200, corps)

            def do_GET(h):
                with outer._verrou:
                    outer._en_cours += 1
                    outer.total += 1
                    outer.max_concurrent = max(outer.max_concurrent, outer._en_cours)
                try:
                    time.sleep(delai_s)     # fenêtre pour révéler une concurrence éventuelle
                    h._repondre()
                finally:
                    with outer._verrou:
                        outer._en_cours -= 1

            do_PUT = do_GET

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
