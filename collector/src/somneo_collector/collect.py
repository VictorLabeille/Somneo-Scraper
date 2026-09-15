"""Le planificateur de collecte (plan §4).

Un planificateur unique, sur l'horloge monotone. Chaque tâche a sa période et sa prochaine
échéance. **Après une interruption, on ne rattrape pas** : la prochaine échéance repart de
maintenant. Jamais de rafale après une reprise.

Toutes les lectures passent par la passerelle (sérialisées, une en vol). Un échec est signalé
au suivi des indisponibilités (`outages.py`), qui ouvre une indisponibilité datée avec sa cause ;
au-delà d'un seuil d'échecs, on demande une redécouverte. Le succès la referme — que l'ait ouverte
cette collecte, celle d'avant la redécouverte ou la redécouverte elle-même : le suivi est partagé,
et survit au remplacement du `Collector`. `capture.py` est ainsi entièrement remplacé — en
lecture seule, comme lui, à deux écritures près. Le rejeu d'un geste de nuit retenu pendant que
le réveil ne répondait pas (incrément 4, `relay.py`) : demandé par l'utilisateur, seulement
différé. Et la sélection d'un profil d'alarme avant de le relire (`PUT wualm {prfnr}`, sans effet
d'après P3), en journée seulement (écart 7).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

from . import clock
from .config import Config
from .gateway import DeviceGateway, Releve
from .nights import NightTracker
from .outages import OutageTracker
from .relay import N_PROFILS
from .store import Store

if TYPE_CHECKING:
    from .relay import Relay

_LOGGER = logging.getLogger(__name__)

ECHECS_AVANT_REDECOUVERTE = 5
REGLAGES = ("wulgt", "wudsk", "wualm", "wualm/aenvs", "wualm/aalms", "wuply")
FICHIERS = ("files/wakeup", "files/lightthemes", "files/dusklightthemes", "files/winddowndusk")
# kind du port dataupload -> suffixe des champs (temp → *tmp)
AGREGATS = {"temp": "tmp", "hum": "hum", "snd": "snd", "lux": "lux"}


class Collector:
    def __init__(self, gateway: DeviceGateway, store: Store, config: Config,
                 on_lost=None, nights: NightTracker | None = None,
                 relay: Relay | None = None, outages: OutageTracker | None = None) -> None:
        self.gw = gateway
        self.store = store
        self.cfg = config
        self._on_lost = on_lost          # coroutine appelée après le seuil d'échecs (redécouverte)
        self._echecs = 0
        # l'indisponibilité en cours : tenue par le superviseur, qui survit à ce Collector (écart
        # 10) ; un suivi propre à défaut, pour les tests de la collecte seule
        self.outages = outages if outages is not None else OutageTracker(store)
        self._stop = asyncio.Event()
        # machine à états des nuits : partagée avec le relais (une seule par processus, voir
        # nights.py) ; une propre à défaut, pour les tests de la collecte seule
        self.nights = nights if nights is not None else NightTracker(store)
        self.relay = relay               # rejoue les gestes retenus (incrément 4)
        self._profils_lus: tuple | None = None   # (jour, aenvs, aalms) de la dernière lecture complète

    # ---- suivi de la disponibilité ------------------------------------------------------
    def _succes(self) -> None:
        self._echecs = 0
        if self.outages.recover():
            _LOGGER.info("réveil de nouveau joignable, indisponibilité close")

    async def _echec(self, r: Releve) -> None:
        self._echecs += 1
        cause = "appareil saturé" if r.status == 500 else "réveil injoignable"
        if self.outages.report(cause):
            _LOGGER.warning("indisponibilité ouverte (%s) après %s", cause, r.error)
        if self._echecs == ECHECS_AVANT_REDECOUVERTE and self._on_lost is not None:
            await self._on_lost()        # redécouverte SSDP : le bail DHCP a peut-être changé

    async def _lire(self, port: str, produit: int = 1) -> Releve:
        r = await self.gw.read(port, produit)
        if r.ok:
            self._succes()
        else:
            await self._echec(r)
        return r

    # ---- tâches -------------------------------------------------------------------------
    async def tache_wusrd(self) -> None:
        r = await self._lire("wusrd")
        if r.ok:
            self.store.add_reading(r.corps, ts=r.observed_at)

    async def tache_wusts(self) -> None:
        r = await self._lire("wusts")
        if r.ok:
            self.store.record_port_change("wusts", r.corps, ts=r.observed_at)
            self.nights.on_wusts((r.corps or {}).get("wusts"), r.observed_at)

    async def tache_wungt(self) -> None:
        r = await self._lire("wungt")
        if r.ok:
            self.store.record_port_change("wungt", r.corps, ts=r.observed_at)
            self.nights.on_wungt(r.corps, r.observed_at)

    async def tache_dataupload(self) -> None:
        for kind, suf in AGREGATS.items():
            r = await self._lire(f"dataupload/{kind}.1/data")
            b = r.corps
            if not b:
                continue
            hist = None
            if f"ab{suf}" in b or f"rl{suf}" in b:
                hist = {"ab": b.get(f"ab{suf}"), "rl": b.get(f"rl{suf}")}
            self.store.add_window_aggregate(kind, b.get(f"av{suf}"), b.get(f"lo{suf}"),
                                            b.get(f"hi{suf}"), hist, ts=r.observed_at)

    async def tache_reglages(self) -> None:
        for port in REGLAGES:
            r = await self._lire(port)
            if r.ok:
                self.store.record_port_change(port, r.corps, ts=r.observed_at)

    async def tache_horloge(self) -> None:
        carte, dev, wut = await clock.mesurer(self.gw)
        self.store.add_clock_check(carte, dev, wut)
        seuil = self.cfg.horloge.seuil_ecart_s
        if dev is not None and abs(dev - carte) > seuil:
            _LOGGER.warning("écart d'horloge %.1f s au-delà du seuil de %.0f s — signalé, "
                            "jamais corrigé (l'heure ne s'écrit pas)", dev - carte, seuil)

    async def tache_liaison(self) -> None:
        for port, produit in (("backend", 0), ("transport", 0), ("device", 1)):
            r = await self._lire(port, produit)
            if not r.ok:
                continue
            self.store.record_port_change(port, r.corps, ts=r.observed_at)
            if port == "device" and r.corps:
                self.store.see_device(r.corps.get("serial"), r.corps.get("type"),
                                      r.corps.get("swversion"), ts=r.observed_at)

    async def tache_fichiers(self) -> None:
        for port in FICHIERS:
            r = await self._lire(port)
            if r.ok:
                self.store.record_port_change(port, r.corps, ts=r.observed_at)

    async def tache_profils(self) -> None:
        """Les seize profils d'alarme, pour l'instantané des réglages (écart 7, plan §4). Relus une
        fois par jour, et de nouveau après un changement de `aenvs`/`aalms`, seulement dans la
        fenêtre de journée (`cfg.profils`, heure de Paris). Chaque lecture commence par une
        sélection, sans effet d'après P3 mais sur les alarmes : jamais le soir. Un échec, ou un
        profil relu autre que celui demandé, interrompt le passage ; le suivant le reprend."""
        maintenant = datetime.fromtimestamp(time.time(), clock.TZ)
        if not self.cfg.profils.debut_h <= maintenant.hour < self.cfg.profils.fin_h:
            return
        etat = (maintenant.date().isoformat(), self.store.last_port_body("wualm/aenvs"),
                self.store.last_port_body("wualm/aalms"))
        if etat == self._profils_lus:
            return
        for n in range(1, N_PROFILS + 1):
            r = await self.gw.read_profile(n)
            if not r.ok:
                await self._echec(r)
                return
            self._succes()
            relu = (r.corps or {}).get("prfnr")
            if relu != n:
                _LOGGER.warning("profil %s relu comme %s : passage interrompu", n, relu)
                return
            self.store.record_profile(n, r.corps, ts=r.observed_at)
        self._profils_lus = etat

    async def tache_gestes(self) -> None:
        """Rejoue les gestes de nuit retenus pendant que le réveil ne répondait pas (cadrage §5)."""
        if self.relay is not None and self.store.pending_gestures():
            await self.relay.replay_pending(self.gw)

    # ---- ordonnancement -----------------------------------------------------------------
    def _plan(self):
        c = self.cfg.cadences
        return [
            ("wusrd", self.tache_wusrd, c.wusrd),
            ("wusts", self.tache_wusts, c.wusts),
            ("wungt", self.tache_wungt, c.wungt),
            ("dataupload", self.tache_dataupload, c.dataupload),
            ("reglages", self.tache_reglages, c.reglages),
            ("horloge", self.tache_horloge, c.horloge),
            ("liaison", self.tache_liaison, c.liaison),
            ("fichiers", self.tache_fichiers, c.fichiers),
            ("profils", self.tache_profils, c.profils),
            ("gestes", self.tache_gestes, c.wungt),
        ]

    def _periode(self, nom: str, defaut: float) -> float:
        """La période, sauf pour l'horloge le jour de la bascule : passe à la minute (§6)."""
        if nom == "horloge" and clock.en_fenetre_bascule(self.cfg.horloge):
            return 60.0
        return defaut

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        """Boucle jusqu'à `stop()`. Chaque tâche part une fois au démarrage, puis à sa période."""
        taches = self._plan()
        echeance = {nom: 0.0 for nom, _, _ in taches}
        _LOGGER.info("collecte démarrée")
        while not self._stop.is_set():
            maintenant = time.monotonic()
            for nom, coro, defaut in taches:
                if maintenant >= echeance[nom]:
                    try:
                        await coro()
                    except Exception:       # une tâche qui lève ne doit pas tuer la boucle
                        _LOGGER.exception("tâche %s", nom)
                    echeance[nom] = time.monotonic() + self._periode(nom, defaut)
                if self._stop.is_set():
                    break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                pass
        _LOGGER.info("collecte arrêtée")
