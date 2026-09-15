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
from .nights import NightTracker
from .outages import OutageTracker
from .relay import Relay
from .state import RuntimeState
from .store import Store

_LOGGER = logging.getLogger(__name__)
BACKOFF_MIN, BACKOFF_MAX = 30.0, 600.0     # redécouverte : 30 s → 10 min
BATTEMENT_S = 60.0


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
        # l'indisponibilité en cours survit aux redécouvertes : un seul suivi, partagé par la
        # collecte et la redécouverte (outages.py, écart 10)
        self.outages = OutageTracker(store)
        # une seule machine des nuits, partagée par la collecte et le relais (nights.py)
        self.nights = NightTracker(store)
        self.relay = Relay(store, self.nights, self.gateway)

    def gateway(self) -> DeviceGateway | None:
        """La passerelle si le réveil répond — aucune indisponibilité en cours —, sinon None : le
        relais n'envoie alors rien, et répond « réveil injoignable » ou retient le geste. Lit le
        suivi, pas la base : une ligne restée ouverte par erreur ne bloque plus le pilotage."""
        if self.gw is None or self.outages.current is not None:
            return None
        return self.gw

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
                return host              # l'indisponibilité reste ouverte : un relevé la fermera
            self.outages.report(cause)
            await self._attendre(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
        return None

    async def _attendre(self, secondes: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=secondes)
        except asyncio.TimeoutError:
            pass

    async def _battre(self) -> None:
        """Le battement, que le réveil réponde ou non : il borne un arrêt du processus (plan §3),
        et la redécouverte fait partie du processus. Il battait dans la collecte, donc se taisait
        pendant une redécouverte."""
        while not self._stop.is_set():
            self.store.heartbeat()
            await self._attendre(BATTEMENT_S)

    async def _attendre_heure(self) -> bool:
        """L'heure d'abord (écart 5) : aucune horloge de la carte ne survit à une coupure, et elle
        repart en retard jusqu'à la synchronisation NTP (docs/radxa.md). Attend le drapeau que
        `timesyncd` pose alors, `attente_synchro_s` au plus ; passé ce délai, la collecte part
        quand même, et `GET /v1/status` le dit. Rend False si l'arrêt est demandé entre-temps."""
        drapeau = self.cfg.synchro_ntp
        fin = time.monotonic() + self.cfg.attente_synchro_s
        while drapeau is not None and not drapeau.exists() and not self._stop.is_set():
            if time.monotonic() >= fin:
                _LOGGER.warning("heure non synchronisée après %.0f s : la collecte part quand "
                                "même, et le statut le signale", self.cfg.attente_synchro_s)
                break
            await self._attendre(min(1.0, fin - time.monotonic()))
        return not self._stop.is_set()

    async def run(self) -> None:
        if not await self._attendre_heure():
            return
        # avant le premier battement : celui en base est encore celui de l'arrêt précédent
        self.outages.repair_stale()
        if self.outages.open_stopped() is not None:
            _LOGGER.warning("reprise après un arrêt du collecteur : indisponibilité nommée")
        battement = asyncio.create_task(self._battre())
        try:
            while not self._stop.is_set():
                host = await self._decouvrir()
                if host is None:
                    return
                self.gw = DeviceGateway(host, self.cfg.espacement_s)
                self.state.reveil_host = host
                self._perte.clear()
                self.collector = Collector(self.gw, self.store, self.cfg,
                                           on_lost=self._signaler_perte, nights=self.nights,
                                           relay=self.relay, outages=self.outages)
                tache = asyncio.create_task(self.collector.run())
                _LOGGER.info("passerelle établie vers %s", host)
                await self._perte.wait()                 # rendu quand la collecte perd le réveil
                self.collector.stop()
                await tache
                await self.gw.close()
                self.gw = None
                if not self._stop.is_set():
                    _LOGGER.warning("réveil reperdu, redécouverte")
        finally:
            battement.cancel()
            await asyncio.gather(battement, return_exceptions=True)
            # dernier signe de vie : la borne basse du prochain « collecteur arrêté », à la seconde
            self.store.heartbeat()

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
    stop = asyncio.Event()
    superviseur = Superviseur(store, cfg, state)
    app = create_app(store, cfg, state, relay=superviseur.relay)
    mdns = discovery.MdnsAnnonce(cfg)

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

    # L'enregistrement zeroconf est synchrone : le lancer HORS de la boucle asyncio (sinon il
    # la bloque et lève EventLoopBlocked). L'annonce mDNS relève de l'incrément 3.
    await asyncio.to_thread(mdns.demarrer)
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
        await asyncio.to_thread(mdns.arreter)
        store.close()
        _LOGGER.info("collecteur arrêté")


def main() -> None:
    asyncio.run(amain(charger()))


if __name__ == "__main__":
    main()
