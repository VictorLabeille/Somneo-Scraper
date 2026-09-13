"""Point d'entrée : un seul processus, un seul worker uvicorn, et les tâches de fond.

Un second worker ouvrirait une seconde connexion au réveil, et l'appareil ferait tomber les
deux : c'est pourquoi uvicorn tourne ici **programmatiquement, avec un seul worker**, jamais via
un lanceur multi-process. L'API démarre même sans réseau ni réveil ; un superviseur cherche le
réveil en boucle et (re)construit la passerelle quand il le trouve ou le reperd (plan §2, §8).
"""
from __future__ import annotations

import asyncio
import logging
import signal
import time

import uvicorn

from . import backup, discovery
from .api import create_app
from .collect import Collector
from .config import Config, charger
from .gateway import DeviceGateway
from .state import RuntimeState
from .store import Store

_LOGGER = logging.getLogger(__name__)
BACKOFF_MIN, BACKOFF_MAX = 30.0, 600.0     # redécouverte : 30 s → 10 min


class Superviseur:
    """Tient une passerelle vivante : découvre le réveil, lance la collecte, rebâtit à la perte."""

    def __init__(self, store: Store, cfg: Config, state: RuntimeState) -> None:
        self.store = store
        self.cfg = cfg
        self.state = state
        self._perte = asyncio.Event()
        self._stop = asyncio.Event()
        self.gw: DeviceGateway | None = None
        self.collector: Collector | None = None

    async def _decouvrir(self) -> str | None:
        if self.cfg.hote_force:
            return self.cfg.hote_force
        backoff = BACKOFF_MIN
        while not self._stop.is_set():
            try:
                host = await asyncio.to_thread(discovery.discover)
            except OSError as exc:                       # carte hors réseau : cause distincte
                _LOGGER.warning("carte hors réseau à la découverte : %s", exc)
                host = None
                cause = "carte hors réseau"
            else:
                cause = "réveil injoignable"
            if host:
                return host
            oid = self.store.open_outage(cause)
            await self._attendre(backoff)
            self.store.close_outage(oid)
            backoff = min(backoff * 2, BACKOFF_MAX)
        return None

    async def _attendre(self, secondes: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=secondes)
        except asyncio.TimeoutError:
            pass

    async def run(self) -> None:
        while not self._stop.is_set():
            host = await self._decouvrir()
            if host is None:
                return
            self.gw = DeviceGateway(host, self.cfg.espacement_s)
            self.state.reveil_host = host
            self._perte.clear()
            self.collector = Collector(self.gw, self.store, self.cfg,
                                       on_lost=self._signaler_perte)
            tache = asyncio.create_task(self.collector.run())
            _LOGGER.info("passerelle établie vers %s", host)
            await self._perte.wait()                     # rendu quand la collecte perd le réveil
            self.collector.stop()
            await tache
            self.gw.close()
            if not self._stop.is_set():
                _LOGGER.warning("réveil reperdu, redécouverte")

    async def _signaler_perte(self) -> None:
        self._perte.set()

    def stop(self) -> None:
        self._stop.set()
        self._perte.set()
        if self.collector is not None:
            self.collector.stop()


async def _boucle_sauvegarde(store: Store, cfg: Config, stop: asyncio.Event) -> None:
    """Une sauvegarde par jour."""
    while not stop.is_set():
        try:
            await asyncio.to_thread(backup.sauvegarder, store.path, cfg.sauvegardes, cfg)
        except Exception:
            _LOGGER.exception("sauvegarde")
        try:
            await asyncio.wait_for(stop.wait(), timeout=86400.0)
        except asyncio.TimeoutError:
            pass


async def amain(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    store = Store(cfg.base)
    state = RuntimeState()
    app = create_app(store, cfg, state)

    stop = asyncio.Event()
    superviseur = Superviseur(store, cfg, state)
    # L'annonce mDNS du collecteur (discovery.MdnsAnnonce) est de l'incrément 3, pas ici.

    server = uvicorn.Server(uvicorn.Config(app, host=cfg.api_host, port=cfg.api_port,
                                           workers=1, log_level="info", lifespan="off"))

    def demander_arret(*_):
        _LOGGER.info("arrêt demandé")
        stop.set()
        superviseur.stop()
        server.should_exit = True

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, demander_arret)

    taches = [
        asyncio.create_task(superviseur.run()),
        asyncio.create_task(_boucle_sauvegarde(store, cfg, stop)),
    ]
    try:
        await server.serve()          # rend la main à l'arrêt (should_exit)
    finally:
        stop.set()
        superviseur.stop()
        for t in taches:
            t.cancel()
        await asyncio.gather(*taches, return_exceptions=True)
        store.close()
        _LOGGER.info("collecteur arrêté")


def main() -> None:
    asyncio.run(amain(charger()))


if __name__ == "__main__":
    main()
