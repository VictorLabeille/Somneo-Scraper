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
# Réglages du coucher de soleil (cadrage §3.D : durée 5-60, intensité 0-25 — différent du lever).
BORNES_SUNSET = {"durat": (5, 60), "curve": (0, 25), "sndlv": (1, 25)}
# Bornes des champs scalaires d'un profil d'alarme (lever), cadrage §3.D et relevé du 2026-09-13.
BORNES_ALARME = {
    "hour": (0, 23), "minute": (0, 59), "days": (0, 254),
    "durat": (5, 40),        # durée du lever de soleil
    "curve": (1, 25),        # intensité du lever
    "sndlv": (1, 25),        # volume
    "powerwake_delta": (1, 59),   # minutes après l'heure de l'alarme (SleepMapper, 2026-09-13)
}
N_PROFILS = 16               # seize emplacements de profil dans le réveil


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

    async def sunset_settings(self, champs: dict) -> Resultat:
        """Règle les paramètres du coucher de soleil (durée, thème, intensité, son, volume) en
        numéros bruts. S'il tourne, on l'**arrête, applique, relance** (décision du 2026-09-13) :
        le firmware ignore un réglage à chaud. Une coupure brève, mais le réglage prend."""
        for nom, bornes in BORNES_SUNSET.items():
            v = champs.get(nom)
            if v is not None and (refus := _hors_bornes(nom, v, bornes)):
                return refus
        payload = {c: champs[c] for c in ("durat", "curve", "ctype", "snddv", "sndch", "sndlv",
                                          "sndss")
                   if champs.get(c) is not None}
        if not payload:
            return _refus("aucun réglage à modifier")
        gw = self._getter()
        if gw is None:
            return self._injoignable("wudsk")
        avant = await gw.read_body("wudsk") or {}
        etait_allume = bool(avant.get("onoff"))
        if etait_allume:
            r = await gw.put("wudsk", {"onoff": False})   # à chaud, un réglage serait ignoré
            if r.injoignable:
                return self._injoignable("wudsk", "réveil injoignable pendant l'arrêt")
        ecriture = await gw.put("wudsk", payload)
        if etait_allume and not ecriture.injoignable:
            await gw.put("wudsk", {"onoff": True})        # relancé comme l'utilisateur l'avait laissé
        attendu = {**payload, "onoff": etait_allume}
        return await self._relire_wudsk(gw, ecriture, attendu)

    async def _relire_wudsk(self, gw: DeviceGateway, ecriture: Releve, attendu: dict) -> Resultat:
        if ecriture.injoignable:
            return self._injoignable("wudsk", f"réveil injoignable pendant l'écriture : {ecriture.error}")
        relu = await gw.read("wudsk")
        etat = relu.corps
        if etat is not None:
            self.store.record_port_change("wudsk", etat, ts=relu.observed_at)
        corps = {"served_at": time.time(), "port": "wudsk", "requested": attendu, "state": etat}
        if not ecriture.ok:
            return Resultat(502, {**corps, "ok": False,
                                  "reason": f"écriture refusée par le réveil : {ecriture.error}"})
        if etat is None:
            return Resultat(502, {**corps, "ok": False, "reason": "relecture impossible"})
        ecarts = {k: etat.get(k) for k, v in attendu.items() if etat.get(k) != v}
        if ecarts:
            return Resultat(502, {**corps, "ok": False, "mismatch": ecarts,
                                  "reason": "réglage non reflété par l'appareil"})
        return Resultat(200, {**corps, "ok": True})

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

    # ---- alarmes (plan §7, décisions du 2026-09-13) -------------------------------------
    async def _profil(self, gw: DeviceGateway, n: int) -> dict | None:
        """Le profil n, sélectionné et relu d'un bloc (`read_profile`, sans effet — P3), historisé
        au changement de ce profil. None si la relecture échoue, ou rend un autre profil : deux
        demandes de l'app, ou la collecte des profils, pouvaient s'intercaler (écart 7)."""
        relu = await gw.read_profile(n)
        profil = relu.corps
        if profil is None or profil.get("prfnr") != n:
            return None
        self.store.record_profile(n, profil, ts=relu.observed_at)
        return profil

    async def _rafraichir_liste(self, gw: DeviceGateway) -> None:
        """Après une écriture d'alarme, remet à jour le miroir de la liste (aenvs, aalms)."""
        for port in ("wualm/aenvs", "wualm/aalms"):
            r = await gw.read(port)
            if r.corps is not None:
                self.store.record_port_change(port, r.corps, ts=r.observed_at)

    async def _ecrire_profil(self, gw: DeviceGateway, n: int, payload: dict,
                             attendu: dict) -> Resultat:
        """`PUT wualm/prfwu` partiel, puis relit le profil et confirme `attendu`. Miroir à jour."""
        r = await gw.put("wualm/prfwu", {"prfnr": n, **payload})
        if r.injoignable:
            return self._injoignable("wualm/prfwu",
                                     f"réveil injoignable pendant l'écriture : {r.error}")
        profil = await self._profil(gw, n)
        if profil is not None:
            await self._rafraichir_liste(gw)
        corps = {"served_at": time.time(), "port": "wualm/prfwu", "n": n, "requested": attendu,
                 "profile": profil}
        if not r.ok:
            return Resultat(502, {**corps, "ok": False,
                                  "reason": f"écriture refusée par le réveil : {r.error}"})
        if profil is None:
            return Resultat(502, {**corps, "ok": False, "reason": "relecture du profil impossible"})
        ecarts = {k: profil.get(k) for k, v in attendu.items() if profil.get(k) != v}
        if ecarts:
            return Resultat(502, {**corps, "ok": False, "mismatch": ecarts,
                                  "reason": "écriture non reflétée par l'appareil"})
        return Resultat(200, {**corps, "ok": True})

    async def get_alarm(self, n: int) -> Resultat:
        if not 1 <= n <= N_PROFILS:
            return _refus(f"numéro de profil hors 1–{N_PROFILS} : {n}")
        gw = self._getter()
        if gw is None:
            return self._injoignable("wualm/prfwu")
        profil = await self._profil(gw, n)
        if profil is None:
            return self._injoignable("wualm/prfwu", "relecture du profil impossible")
        return Resultat(200, {"served_at": time.time(), "n": n, "profile": profil})

    async def set_alarm(self, n: int, champs: dict) -> Resultat:
        """Édite un profil : `PUT wualm/prfwu` partiel, en numéros bruts (plan §7). Les bornes
        scalaires sont vérifiées avant l'envoi ; un numéro de thème/son passe tel quel, la
        relecture est son garde-fou. `sndss` n'est pas écrit tant que `probes/sndss.py` ne l'a
        pas mesuré."""
        if not 1 <= n <= N_PROFILS:
            return _refus(f"numéro de profil hors 1–{N_PROFILS} : {n}")
        pw = champs.get("powerwake")
        if isinstance(pw, dict) and pw.get("on") and pw.get("delta") is None:
            return _refus("PowerWake activé sans délai (minutes après l'alarme)")
        for nom in ("hour", "minute", "days", "durat", "curve", "sndlv"):
            v = champs.get(nom)
            if v is not None and (refus := _hors_bornes(nom, v, BORNES_ALARME[nom])):
                return refus
        if isinstance(pw, dict) and pw.get("on"):
            if refus := _hors_bornes("PowerWake (min après l'alarme)", pw["delta"],
                                     BORNES_ALARME["powerwake_delta"]):
                return refus
        gw = self._getter()
        if gw is None:
            return self._injoignable("wualm/prfwu")
        courant = await self._profil(gw, n)
        if courant is None:
            return self._injoignable("wualm/prfwu", "relecture du profil impossible")

        payload, attendu = {}, {}
        # sndss (départ en douceur) passe en numéro brut, non borné : mesuré le 2026-09-13
        # (probes/sndss.py) librement inscriptible et repris verbatim jusqu'à 300, sans effet de
        # bord ; son SENS reste inconnu, la relecture est son seul garde-fou.
        for cle, champ in (("enabled", "prfen"), ("days", "daynm"), ("ctype", "ctype"),
                           ("curve", "curve"), ("durat", "durat"), ("snddv", "snddv"),
                           ("sndch", "sndch"), ("sndlv", "sndlv"), ("sndss", "sndss")):
            if champs.get(cle) is not None:
                payload[champ] = attendu[champ] = champs[cle]
        if champs.get("hour") is not None:
            payload["almhr"] = attendu["almhr"] = champs["hour"]
        if champs.get("minute") is not None:
            payload["almmn"] = attendu["almmn"] = champs["minute"]
        pw = champs.get("powerwake")
        if isinstance(pw, dict):
            if pw.get("on"):
                base = (champs.get("hour", courant.get("almhr")) * 60
                        + champs.get("minute", courant.get("almmn")) + pw["delta"]) % (24 * 60)
                payload.update(pwrsz=1, pszhr=base // 60, pszmn=base % 60)
            else:
                payload.update(pwrsz=0, pszhr=0, pszmn=0)
            attendu.update(pwrsz=payload["pwrsz"], pszhr=payload["pszhr"], pszmn=payload["pszmn"])
        if not payload:
            return _refus("aucun champ à modifier")
        return await self._ecrire_profil(gw, n, payload, attendu)

    async def create_alarm(self) -> Resultat:
        """Rend visible le premier emplacement masqué, désactivé (`prfvs: true, prfen: false`).
        Seize visibles → `409`, jamais d'écrasement (cadrage §3.D)."""
        gw = self._getter()
        if gw is None:
            return self._injoignable("wualm/aenvs")
        envs = await gw.read_body("wualm/aenvs") or {}
        prfvs = envs.get("prfvs") or []
        libre = next((i + 1 for i, v in enumerate(prfvs) if not v), None)
        if libre is None:
            return Resultat(409, {"served_at": time.time(), "ok": False,
                                  "reason": f"les {N_PROFILS} emplacements d'alarme sont occupés"})
        res = await self._ecrire_profil(gw, libre, {"prfvs": True, "prfen": False},
                                        {"prfvs": True, "prfen": False})
        if res.status == 200:
            return Resultat(201, res.body)
        return res

    async def delete_alarm(self, n: int) -> Resultat:
        """Masque le profil (`prfen`/`prfvs` à false), réglages conservés — pas de remise à
        l'usine (décision du 2026-09-13). Aucune alarme masquée ne peut sonner (cadrage §3.D)."""
        if not 1 <= n <= N_PROFILS:
            return _refus(f"numéro de profil hors 1–{N_PROFILS} : {n}")
        gw = self._getter()
        if gw is None:
            return self._injoignable("wualm/prfwu")
        return await self._ecrire_profil(gw, n, {"prfen": False, "prfvs": False},
                                         {"prfen": False, "prfvs": False})

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
