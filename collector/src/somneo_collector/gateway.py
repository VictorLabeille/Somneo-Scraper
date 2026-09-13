"""La passerelle : seul accès au réveil, seule à importer `pysomneo`.

Deux invariants tiennent la connexion unique **par construction** (plan §2) :

1. **Aucun autre module n'importe `pysomneo`.** Un test le vérifie (`tests/test_isolation.py`).
2. **Une requête en vol, jamais deux.** Un `asyncio.Lock` sérialise tous les appels, quel que
   soit le nombre d'appelants ; `pysomneo` est synchrone (`requests`), donc chaque appel bloquant
   part dans un thread (`asyncio.to_thread`) **en tenant le verrou** — le thread ne se lance
   qu'une fois le précédent fini. Le pool d'aiohttp/urllib3 n'a donc jamais de file : le défaut
   du banc de la PR #26 (une requête en file expire dans `timeout.connect`) ne peut pas survenir.

Les lectures rendent le **corps JSON brut** (ce que l'appareil dit, pas ce que la bibliothèque
en interprète). Un `422` (`SomneoInvalidURLError`) et un `500` (`HTTPError` via `raise_for_status`)
ne lèvent pas : ils reviennent dans le champ `error` du relevé, pour être journalisés au lieu
d'être perdus — comme le fait `probes/somneo_probe.py`.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from pysomneo import Somneo
from pysomneo.api import SomneoInvalidURLError
from requests.exceptions import HTTPError, RequestException

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Releve:
    """Le résultat d'une requête au réveil, jamais une exception."""

    produit: int
    port: str
    ok: bool
    status: int | None = None
    body: Any = None
    error: str | None = None
    ms: float = 0.0
    observed_at: float = 0.0     # time.time() au retour, pour dater le miroir

    @property
    def corps(self) -> dict | None:
        return self.body if self.ok and isinstance(self.body, dict) else None


def _chemin(produit: int, port: str) -> str:
    """Chemin passé à `_internal_call`. Produit 1 : relatif (base_url). Produit 0 : absolu."""
    if produit == 1:
        return port
    return f"/di/v1/products/{produit}/{port}"


class DeviceGateway:
    """Unique porte vers le réveil : une session `pysomneo`, un verrou, un espacement."""

    def __init__(self, host: str, espacement_s: float = 0.2) -> None:
        self._somneo = Somneo(host=host)
        self._espacement = espacement_s
        self._lock = asyncio.Lock()
        self._fin_derniere = 0.0     # time.monotonic() de la fin de la dernière requête

    @property
    def host(self) -> str:
        return self._somneo._host

    @property
    def somneo(self) -> Somneo:
        """Accès à l'objet `pysomneo` pour les écritures de haut niveau (incréments ultérieurs).

        Confiné ici : aucun autre module ne le touche. Les appels passent quand même par
        `run()`, pour rester sérialisés."""
        return self._somneo

    async def _serialise(self, fn: Callable[[], Any]) -> Any:
        """Exécute `fn` (bloquant) sous le verrou, en respectant l'espacement. Une seule à la fois."""
        async with self._lock:
            attente = self._fin_derniere + self._espacement - time.monotonic()
            if attente > 0:
                await asyncio.sleep(attente)
            try:
                return await asyncio.to_thread(fn)
            finally:
                self._fin_derniere = time.monotonic()

    async def read(self, port: str, produit: int = 1) -> Releve:
        """GET sérialisé. Ne lève jamais : `422` et `500` reviennent dans `error`."""
        chemin = _chemin(produit, port)

        def appel() -> Releve:
            t0 = time.monotonic()
            try:
                body = self._somneo._client._internal_call("GET", chemin)
                return Releve(produit, port, True, 200, body,
                              ms=round((time.monotonic() - t0) * 1000, 1),
                              observed_at=time.time())
            except SomneoInvalidURLError as exc:
                return Releve(produit, port, False, 422, error=str(exc),
                              ms=round((time.monotonic() - t0) * 1000, 1),
                              observed_at=time.time())
            except HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                return Releve(produit, port, False, status, error=f"HTTP {status}",
                              ms=round((time.monotonic() - t0) * 1000, 1),
                              observed_at=time.time())
            except (RequestException, OSError) as exc:
                return Releve(produit, port, False, None,
                              error=f"{type(exc).__name__}: {exc}",
                              ms=round((time.monotonic() - t0) * 1000, 1),
                              observed_at=time.time())

        return await self._serialise(appel)

    async def read_body(self, port: str, produit: int = 1) -> dict | None:
        return (await self.read(port, produit)).corps

    async def run(self, fn: Callable[[], Any]) -> Any:
        """Exécute un appel `pysomneo` arbitraire (écriture de haut niveau), sérialisé.

        Sert au relais du pilotage : une écriture ne double jamais une lecture de collecte."""
        return await self._serialise(fn)

    async def put(self, port: str, payload: dict, produit: int = 1) -> Releve:
        """PUT sérialisé pour les ports que `pysomneo` ne couvre pas (ex. `wungt` : gestes de nuit).

        Ne lève pas : un `422`/`500` revient dans `error`, comme pour `read`."""
        chemin = _chemin(produit, port)

        def appel() -> Releve:
            t0 = time.monotonic()
            try:
                body = self._somneo._client._internal_call("PUT", chemin, payload=payload)
                return Releve(produit, port, True, 200, body,
                              ms=round((time.monotonic() - t0) * 1000, 1),
                              observed_at=time.time())
            except SomneoInvalidURLError:
                return Releve(produit, port, False, 422, error="HTTP 422",
                              ms=round((time.monotonic() - t0) * 1000, 1))
            except HTTPError as exc:
                status = getattr(exc.response, "status_code", None)
                return Releve(produit, port, False, status, error=f"HTTP {status}",
                              ms=round((time.monotonic() - t0) * 1000, 1))
            except (RequestException, OSError) as exc:
                return Releve(produit, port, False, None, error=f"{type(exc).__name__}: {exc}",
                              ms=round((time.monotonic() - t0) * 1000, 1))

        return await self._serialise(appel)

    def close(self) -> None:
        try:
            self._somneo._client.session.close()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("fermeture de session : %s", exc)
