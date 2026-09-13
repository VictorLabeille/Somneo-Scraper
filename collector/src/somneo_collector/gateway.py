"""La passerelle : seul accès au réveil, seule à importer `pysomneo`.

`pysomneo` 6.0, asynchrone (aiohttp), épinglée au commit `13ec0c5` du fork — `365d313` plus le
`limit=1` de la PR #26 (plan §0, §8). Deux invariants tiennent la connexion unique **par
construction** (plan §2) :

1. **Aucun autre module n'importe `pysomneo`.** Un test le vérifie (`tests/test_isolation.py`).
2. **Une requête en vol, jamais deux.** Un `asyncio.Lock` sérialise tous les appels, quel que
   soit le nombre d'appelants : chaque coroutine de `pysomneo` s'exécute **en tenant le verrou**.
   Le `limit=1` du connecteur va dans le même sens, mais la garantie ne dépend pas de la
   bibliothèque : c'est ce verrou qui la donne. Le défaut du banc de la PR #26 (une requête en
   file expire dans `timeout.connect`) ne peut donc pas survenir.

Les lectures rendent le **corps JSON brut** (ce que l'appareil dit, pas ce que la bibliothèque
en interprète). Un `422` (`SomneoInvalidURLError`) et un `500` (`ClientResponseError`) ne lèvent
pas : ils reviennent dans le champ `error` du relevé, pour être journalisés au lieu d'être
perdus — comme le fait `probes/somneo_probe.py`. Un `500` remonte **une seule fois** :
`raise_for_status` est hors de la boucle de réessais de la 6.0. La 5.0.6 synchrone, elle, le
réessayait douze fois en 39 s et le faisait passer pour une panne de connexion (plan §2). La 6.0
a le travers inverse sur le `422` : réessayé, puis remonté en `SomneoConnectionError` — la
passerelle déplie la cause pour le rendre comme un `422`.

Les écritures de haut niveau passent par les méthodes de `pysomneo` (plan §2), **cache vidé
avant chaque appel** (plan §7). L'objet `Somneo` n'est jamais rafraîchi par la collecte, qui lit
les corps bruts, et ses méthodes envoient des charges complètes reprises de ce cache
(`toggle_light` réécrit tout `wulgt`) : sans cela, un réglage fait à la façade entre deux
commandes serait écrasé sans bruit. Une écriture dit si les requêtes sont passées ; **si
l'appareil a pris la valeur, c'est la relecture du relais qui le dit** (`relay.py`). Ce module
est le seul à connaître l'API de `pysomneo` : noms des méthodes, échelles, numérotation.
"""
from __future__ import annotations

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from aiohttp import ClientError, ClientResponseError
from pysomneo import Somneo
from pysomneo.api import SomneoConnectionError, SomneoInvalidURLError

_LOGGER = logging.getLogger(__name__)

# Caches d'état de l'objet `Somneo`, remis à None avant chaque écriture (plan §7). Les thèmes
# (`files/*`) ne changent pas : leur cache est gardé, il épargne quatre lectures par écriture.
_CACHES_ETAT = ("alarm_status", "light_data", "sensor_data", "sunset_data",
                "enabled_alarms", "time_alarms", "snoozetime", "player")
# Ce que lève `pysomneo` quand un corps n'a pas la forme qu'il attend, ou qu'un nom de thème lui
# est inconnu. Capturé pour une écriture seulement : une lecture ne passe pas par ses
# interprétations, une telle erreur y serait un bogue du collecteur.
_ERREURS_PYSOMNEO = (KeyError, IndexError, TypeError, ValueError)


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
    injoignable: bool = False    # la requête n'est pas passée (connexion) : inutile de relire

    @property
    def corps(self) -> dict | None:
        return self.body if self.ok and isinstance(self.body, dict) else None


def _chemin(produit: int, port: str) -> str:
    """Chemin passé à `_internal_call`. Produit 1 : relatif (base_url). Produit 0 : absolu."""
    if produit == 1:
        return port
    return f"/di/v1/products/{produit}/{port}"


async def _capturer(produit: int, port: str, appel: Callable[[], Awaitable[Any]],
                    ecriture: bool = False) -> Releve:
    """Attend `appel()` et en fait un `Releve`. Ne lève pas (sauf bogue d'une lecture)."""
    t0 = time.monotonic()

    def releve(ok: bool, status: int | None, body: Any = None, error: str | None = None,
               injoignable: bool = False) -> Releve:
        return Releve(produit, port, ok, status, body, error,
                      ms=round((time.monotonic() - t0) * 1000, 1), observed_at=time.time(),
                      injoignable=injoignable)

    try:
        return releve(True, 200, await appel())
    except SomneoInvalidURLError as exc:
        return releve(False, 422, error=str(exc) or "HTTP 422")
    except ClientResponseError as exc:
        return releve(False, exc.status, error=f"HTTP {exc.status}")
    except SomneoConnectionError as exc:
        # la 6.0 réessaie le 422 : `SomneoInvalidURLError` hérite de `ClientError`, que sa boucle
        # attrape, et il finit en `SomneoConnectionError`. Ce n'est pas une panne de connexion.
        if isinstance(exc.__cause__, SomneoInvalidURLError):
            return releve(False, 422, error=str(exc.__cause__) or "HTTP 422")
        return releve(False, None, error=f"{type(exc).__name__}: {exc}", injoignable=True)
    except (ClientError, asyncio.TimeoutError, OSError) as exc:
        # dont SomneoConnectionError, levée après les trois tentatives de pysomneo
        return releve(False, None, error=f"{type(exc).__name__}: {exc}", injoignable=True)
    except _ERREURS_PYSOMNEO as exc:
        if not ecriture:
            raise
        return releve(False, None, error=f"pysomneo, {type(exc).__name__}: {exc}")


def _vers_255(level: int) -> int:
    """Le plus petit `brightness` (0-255) que `pysomneo` ramène exactement à `level` (1-25) :
    il calcule `int(brightness / 255 * 25)`, qui tronque."""
    b = math.ceil(level * 255 / 25)
    while int(b / 255 * 25) < level:
        b += 1
    return b


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

    async def _serialise(self, appel: Callable[[], Awaitable[Any]]) -> Any:
        """Attend `appel()` sous le verrou, en respectant l'espacement. Un seul à la fois."""
        async with self._lock:
            attente = self._fin_derniere + self._espacement - time.monotonic()
            if attente > 0:
                await asyncio.sleep(attente)
            try:
                return await appel()
            finally:
                self._fin_derniere = time.monotonic()

    async def read(self, port: str, produit: int = 1) -> Releve:
        """GET sérialisé. Ne lève jamais : `422` et `500` reviennent dans `error`."""
        chemin = _chemin(produit, port)
        return await self._serialise(lambda: _capturer(
            produit, port, lambda: self._somneo._client._internal_call("GET", chemin)))

    async def read_body(self, port: str, produit: int = 1) -> dict | None:
        return (await self.read(port, produit)).corps

    async def run(self, appel: Callable[[], Awaitable[Any]]) -> Any:
        """Attend une coroutine arbitraire, sérialisée. Ne capture rien : c'est l'appelant qui
        traite les erreurs (sert aux tests de l'invariant)."""
        return await self._serialise(appel)

    async def put(self, port: str, payload: dict, produit: int = 1) -> Releve:
        """PUT sérialisé pour les ports que `pysomneo` ne couvre pas (ex. `wungt` : gestes de nuit).

        Ne lève pas : un `422`/`500` revient dans `error`, comme pour `read`."""
        chemin = _chemin(produit, port)
        return await self._serialise(lambda: _capturer(
            produit, port,
            lambda: self._somneo._client._internal_call("PUT", chemin, payload=payload)))

    async def write(self, port: str, appel: Callable[[Somneo], Awaitable[Any]]) -> Releve:
        """Écriture par une méthode de `pysomneo`, sérialisée, **cache vidé d'abord** (plan §7).

        La méthode relit donc l'appareil avant de construire sa charge. Le relevé rendu n'a pas de
        corps : il dit si les requêtes sont passées, pas ce que l'appareil a pris — c'est la
        relecture du relais qui le dit. `port` ne sert qu'à nommer le relevé."""
        async def appel_frais() -> None:
            for attr in _CACHES_ETAT:
                setattr(self._somneo, attr, None)
            self._somneo.data = {}
            await appel(self._somneo)

        return await self._serialise(lambda: _capturer(1, port, appel_frais, ecriture=True))

    # ---- écritures de haut niveau (relais du pilotage, incrément 4) ----------------------
    async def toggle_light(self, on: bool, level: int | None = None) -> Releve:
        """Lampe. `level` sur l'échelle de l'appareil (`ltlvl`, 1-25) ; `pysomneo` attend 0-255.
        Un niveau nul lui serait invisible (`if brightness:`) : le relais ne l'envoie pas."""
        brightness = _vers_255(level) if level is not None else None
        return await self.write("wulgt", lambda s: s.toggle_light(on, brightness))

    async def toggle_night_light(self, on: bool) -> Releve:
        return await self.write("wulgt", lambda s: s.toggle_night_light(on))

    async def toggle_sunset(self, on: bool) -> Releve:
        return await self.write("wudsk", lambda s: s.toggle_sunset(on))

    async def set_snooze_time(self, minutes: int) -> Releve:
        """Durée du rappel, globale à toutes les alarmes (`wualm.snztm`)."""
        return await self.write("wualm", lambda s: s.set_snooze_time(minutes))

    async def close(self) -> None:
        try:
            await self._somneo._client.session.close()
        except Exception as exc:  # noqa: BLE001
            _LOGGER.debug("fermeture de session : %s", exc)
