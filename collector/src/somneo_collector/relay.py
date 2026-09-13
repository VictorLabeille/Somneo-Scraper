"""Le relais du pilotage (incrément 4, plan §7) : écrire, relire, confirmer.

Chaque commande suit la même forme : bornes vérifiées **avant l'envoi** (cadrage §3.D — elles
diffèrent d'un écran à l'autre et ne s'uniformisent pas), écriture par la passerelle (sérialisée),
**relecture du port**, puis réponse avec l'état relu. Si la relecture ne montre pas la valeur
demandée, c'est un échec, même après un `200` : l'appareil fait foi, pas sa réponse. Jamais
d'affichage optimiste — sur une alarme, l'illusion se paie au réveil.

La relecture met aussi le miroir à jour (`port_change`) : l'app qui relit `GET /v1/device` voit
l'état confirmé sans attendre le prochain cycle de collecte.

Les gestes de nuit (`wungt`) ont une règle de plus : un appui n'est **jamais perdu** (cadrage §5).
Réveil injoignable → l'heure de l'appui est retenue, la réponse dit « en attente du réveil », et
le geste est rejoué dès que le réveil répond (`replay_pending`, appelé par la collecte).

Codes rendus : `200` fait (ou rien à faire), `201` geste pris par le réveil, `202` geste retenu,
`409` rien à clore, `422` hors bornes, `502` refusé ou non reflété, `503` réveil injoignable.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from .gateway import DeviceGateway, Releve
from .nights import NightTracker
from .store import Store
from .store.db import GESTE_ABANDONNE, GESTE_REGLE

_LOGGER = logging.getLogger(__name__)

# Bornes relevées dans SleepMapper (dépôt SleepMaxxer, docs/sleepmapper/README.md), cadrage §3.D.
BORNES_LAMPE = (1, 25)       # `ltlvl`. 0 n'est pas relevé dans SleepMapper et pysomneo l'ignore :
                             # éteindre, c'est `on: false`
BORNES_RAPPEL = (1, 20)      # minutes, global à toutes les alarmes


@dataclass(frozen=True)
class Resultat:
    """Code HTTP et corps d'une réponse du relais. Le corps porte l'état relu quand il existe."""

    status: int
    body: dict


def _refus(raison: str) -> Resultat:
    return Resultat(422, {"served_at": time.time(), "ok": False, "reason": raison})


def _hors_bornes(nom: str, valeur: int, bornes: tuple[int, int]) -> Resultat | None:
    lo, hi = bornes
    if not lo <= valeur <= hi:
        return _refus(f"{nom} hors bornes : {valeur} (attendu {lo}–{hi})")
    return None


class Relay:
    """Relaie les commandes de l'app vers le réveil, et les gestes de nuit (plan §5, §7).

    `gateway_getter` rend la passerelle si le réveil répond, None sinon : rien n'est alors
    envoyé. Les gestes sont traités un par un (verrou) : deux appuis rapprochés ne peuvent pas
    tous deux se croire les premiers."""

    def __init__(self, store: Store, nights: NightTracker,
                 gateway_getter: Callable[[], DeviceGateway | None]) -> None:
        self.store = store
        self.nights = nights
        self._getter = gateway_getter
        self._verrou_gestes = asyncio.Lock()

    # ---- le motif commun ----------------------------------------------------------------
    def _miroir(self, port: str) -> dict:
        vu = self.store.last_port(port)
        return {"state": vu["body"] if vu else None, "observed_at": vu["observed_at"] if vu else None}

    def _injoignable(self, port: str, raison: str = "réveil injoignable") -> Resultat:
        """Rien n'est parti, ou rien n'est revenu. L'état servi est celui du miroir, daté : c'est
        la dernière chose que l'appareil a dite (ancienne valeur conservée, cadrage §3.D)."""
        return Resultat(503, {"served_at": time.time(), "ok": False, "port": port,
                              "reason": raison, **self._miroir(port)})

    async def _ecrire_relire(self, gw: DeviceGateway, port: str,
                             ecriture: Callable[[], Awaitable[Releve]], attendu: dict) -> Resultat:
        """Écrire → relire → confirmer (plan §7). `attendu` : champs que la relecture doit montrer."""
        r = await ecriture()
        if r.injoignable:
            # même panne à la relecture, même attente : on ne relit pas
            return self._injoignable(port, f"réveil injoignable pendant l'écriture : {r.error}")
        relu = await gw.read(port)
        etat = relu.corps
        if etat is not None:
            self.store.record_port_change(port, etat, ts=relu.observed_at)
        corps = {"served_at": time.time(), "port": port, "requested": attendu, "state": etat,
                 "observed_at": relu.observed_at if etat is not None else None}
        if not r.ok:
            return Resultat(502, {**corps, "ok": False,
                                  "reason": f"écriture refusée par le réveil : {r.error}"})
        if etat is None:
            return Resultat(502, {**corps, "ok": False,
                                  "reason": f"relecture impossible : {relu.error}"})
        ecarts = {k: etat.get(k) for k, v in attendu.items() if etat.get(k) != v}
        if ecarts:
            return Resultat(502, {**corps, "ok": False, "mismatch": ecarts,
                                  "reason": "écriture non reflétée par l'appareil"})
        return Resultat(200, {**corps, "ok": True})

    # ---- commandes simples --------------------------------------------------------------
    async def light(self, on: bool, level: int | None = None) -> Resultat:
        if level is not None:
            if not on:
                return _refus("l'intensité ne se règle que lampe allumée")
            if refus := _hors_bornes("intensité de la lampe", level, BORNES_LAMPE):
                return refus
        gw = self._getter()
        if gw is None:
            return self._injoignable("wulgt")
        attendu = {"onoff": on, **({"ltlvl": level} if level is not None else {})}
        return await self._ecrire_relire(gw, "wulgt", lambda: gw.toggle_light(on, level), attendu)

    async def nightlight(self, on: bool) -> Resultat:
        gw = self._getter()
        if gw is None:
            return self._injoignable("wulgt")
        return await self._ecrire_relire(gw, "wulgt", lambda: gw.toggle_night_light(on),
                                         {"ngtlt": on})

    async def sunset(self, on: bool) -> Resultat:
        gw = self._getter()
        if gw is None:
            return self._injoignable("wudsk")
        return await self._ecrire_relire(gw, "wudsk", lambda: gw.toggle_sunset(on), {"onoff": on})

    async def snooze(self, minutes: int) -> Resultat:
        if refus := _hors_bornes("durée du rappel", minutes, BORNES_RAPPEL):
            return refus
        gw = self._getter()
        if gw is None:
            return self._injoignable("wualm")
        return await self._ecrire_relire(gw, "wualm", lambda: gw.set_snooze_time(minutes),
                                         {"snztm": minutes})

    # ---- gestes de nuit (plan §5) -------------------------------------------------------
    def _nuit(self, night_id: int, device: str) -> dict:
        return {"served_at": time.time(), "device": device, "night": self.store.get_night(night_id)}

    async def _geste(self, gw: DeviceGateway, night: bool) -> tuple[str, dict | None]:
        """Écrit `{"night": …}` dans `wungt` puis relit. Rend `ok`, `injoignable` ou `refus`.

        `tg2bd`/`tendb` ne s'envoient pas : le réveil ne les prend jamais (P1), il date la session
        de l'instant où il reçoit la requête. Rejouer est sans risque : le réveil ignore une
        ouverture sur une session ouverte, une fermeture sur une session close."""
        r = await gw.put("wungt", {"night": night})
        if r.injoignable:
            return "injoignable", None
        relu = await gw.read("wungt")
        if relu.corps is None:
            return "injoignable", None
        self.store.record_port_change("wungt", relu.corps, ts=relu.observed_at)
        if not r.ok or bool(relu.corps.get("night")) != night:
            return "refus", relu.corps
        return "ok", relu.corps

    async def bedtime(self) -> Resultat:
        """« Je me couche ». `201` : le réveil a pris la session (relue). `202` : l'appui est
        retenu, en attente du réveil — jamais perdu. `200` : une nuit est déjà en cours, rien
        n'est écrit (le réveil ignorerait l'appui ; décision du 2026-09-13)."""
        ts = time.time()
        async with self._verrou_gestes:
            courante = self.nights.current()
            if courante is not None:
                if courante["state"] == "pending_device":
                    return Resultat(202, self._nuit(courante["id"], "en attente du réveil"))
                return Resultat(200, self._nuit(courante["id"], "déjà en cours"))
            gw = self._getter()
            if gw is not None:
                etat, relu = await self._geste(gw, True)
                if etat == "ok":
                    nid = self.nights.bedtime_taken(ts, relu.get("tg2bd"))
                    return Resultat(201, self._nuit(nid, "pris"))
                _LOGGER.warning("coucher non pris par le réveil (%s) : retenu", etat)
            return Resultat(202, self._nuit(self.nights.bedtime_pending(ts), "en attente du réveil"))

    async def risetime(self) -> Resultat:
        """« Je me lève » : lever = l'heure de l'appui, confirmé. La nuit est close dans le
        collecteur quoi qu'il arrive ; la session du réveil l'est aussi, ou le sera à son retour."""
        ts = time.time()
        async with self._verrou_gestes:
            nuit = self.nights.closable()
            if nuit is None:
                return Resultat(409, {"served_at": time.time(), "ok": False,
                                      "reason": "aucune nuit en cours"})
            nid = nuit["id"]
            if nuit["state"] != "open":
                # retenue (le réveil n'a jamais reçu le coucher), ou déjà close par le firmware
                # en attendant la fin de l'alarme : aucune session à fermer dans le réveil
                self.nights.rise_confirmed(nid, ts)
                return Resultat(200, self._nuit(nid, "rien à fermer dans le réveil"))
            gw = self._getter()
            if gw is not None:
                etat, _ = await self._geste(gw, False)
                if etat == "ok":
                    self.nights.rise_confirmed(nid, ts)
                    return Resultat(201, self._nuit(nid, "pris"))
                _LOGGER.warning("lever non pris par le réveil (%s) : retenu", etat)
            self.nights.rise_confirmed(nid, ts)
            self.store.add_pending_gesture("risetime", ts, nid)
            return Resultat(202, self._nuit(nid, "en attente du réveil"))

    async def replay_pending(self, gw: DeviceGateway) -> None:
        """Rejoue les gestes retenus, dans l'ordre des appuis (appelé par la collecte).

        S'arrête au premier échec de connexion : le réveil ne répond toujours pas, on réessaiera
        au tour suivant. **Abandonne un geste que le réveil refuse** — une boucle qui réessaie un
        refus n'aboutit jamais (`AGENTS.md`, les 489 `PUT` du 2026-09-12)."""
        async with self._verrou_gestes:
            for g in self.store.pending_gestures():
                nuit = self.store.get_night(g["night_id"]) if g["night_id"] is not None else None
                if g["kind"] == "bedtime" and (nuit is None or nuit["state"] != "pending_device"):
                    self.store.settle_gesture(g["id"], GESTE_REGLE)   # adoptée ou close entre-temps
                    continue
                etat, relu = await self._geste(gw, g["kind"] == "bedtime")
                if etat == "injoignable":
                    return
                if etat == "refus":
                    _LOGGER.warning("geste %s (appui à %s) refusé par le réveil : abandonné",
                                    g["kind"], g["ts"])
                    self.store.settle_gesture(g["id"], GESTE_ABANDONNE)
                    if g["kind"] == "bedtime":
                        self.nights.pending_refused(nuit["id"])
                    continue
                if g["kind"] == "bedtime":
                    self.nights.pending_taken(nuit["id"], relu.get("tg2bd"))
                self.store.settle_gesture(g["id"], GESTE_REGLE)
