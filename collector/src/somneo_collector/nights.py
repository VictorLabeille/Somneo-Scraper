"""Machine à états des sessions de nuit (plan §5, règles des quatre matins d'alarme mesurés).

Deux sources de transitions, une seule machine :

- **ce que la collecte observe** de `wungt` et `wusts` (incrément 2) — un geste fait par
  SleepMapper, la clôture par le firmware, la fin de l'alarme ;
- **les gestes relayés par le collecteur** (incrément 4, `relay.py`) — le coucher et le lever de
  l'app, dont l'heure est celle de l'**appui**, confirmée.

Une seule instance par processus, que la collecte et le relais partagent : deux instances
tiendraient deux idées de la nuit ouverte, et l'une ouvrirait une nuit que l'autre ne voit pas.

**Les heures sont dans le référentiel du collecteur (NTP)** — contrat §F : le coucher est
l'instant où on OBSERVE `wungt` passer à `night: true` (à ~30 s près), ou celui de l'appui relayé,
pas la date `tg2bd` inscrite par le réveil sur son horloge `wutim`. `tg2bd`/`tendb` sont conservés
bruts pour la traçabilité, jamais servis comme l'heure.

Faits qui commandent la machine (`docs/somneo-api.md` §4) :
- C'est le firmware qui clôt la nuit, à l'heure programmée de l'alarme, avant tout geste :
  `wungt` repasse à `night: false` seul. On ne peut donc pas attendre un geste pour fermer.
- La fin de l'alarme (le lever *estimé*) arrive 1 à 15 min APRÈS la clôture de `wungt` : c'est
  la chute du bit 11 de `wusts`. La clôture et le lever sont donc décorrélés dans le temps.
- Sans alarme, `wungt` repasse à `false` par expiration (≈ 12 h) : nuit anormale, pas de lever.
- Le double appui est filtré par l'appareil (`tg2bd` ne rebouge pas) : rien à faire. Côté relais,
  un « je me couche » pendant une nuit ouverte ne fait rien non plus (décision du 2026-09-13).
- `tg2bd` ne s'écrit pas (P1) : l'heure d'un appui retenu pendant que le réveil ne répondait pas
  vit ICI ; le réveil datera la session de l'instant où il la recevra.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Store

_LOGGER = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Paris")
BIT_ACTIF = 1 << 11        # `wusts` : l'appareil est en séquence de réveil/alarme


def _jour(ts: float) -> str:
    """Jour local (Europe/Paris) d'un instant : une nuit appartient au jour de son coucher."""
    return datetime.fromtimestamp(ts, TZ).date().isoformat()


class NightTracker:
    """Consomme les observations de `wungt` et `wusts`, et les gestes relayés ; tient `night`.

    Reprend son état de la base au démarrage : une nuit ouverte, ou une close en attente du
    lever (risetime NULL), survivent à un redémarrage du collecteur."""

    def __init__(self, store: Store) -> None:
        self.store = store
        ouverte = store.get_open_night()
        attente = store.get_awaiting_night()
        self._open_id = ouverte["id"] if ouverte else None
        self._awaiting_id = attente["id"] if attente else None
        self._alarme_active = False
        self._alarme_depuis_ouverture = False
        self._alarme_tombee_a: float | None = None

    # ---- observations (collecte) --------------------------------------------------------
    def on_wusts(self, wusts_value, ts: float) -> None:
        if not isinstance(wusts_value, int):
            return
        actif = bool(wusts_value & BIT_ACTIF)
        if actif and not self._alarme_active and self._open_id is not None:
            self._alarme_depuis_ouverture = True
        if not actif and self._alarme_active:
            self._alarme_tombee_a = ts                 # bit 11 retombé : fin de l'alarme
            if self._awaiting_id is not None:          # une nuit attendait ce lever
                self.store.night_set_rise(self._awaiting_id, ts)
                _LOGGER.info("nuit %s : lever estimé à la fin de l'alarme", self._awaiting_id)
                self._awaiting_id = None
        self._alarme_active = actif

    def on_wungt(self, body: dict | None, ts: float) -> None:
        if not isinstance(body, dict):
            return
        night = bool(body.get("night"))
        if night and self._open_id is None:
            self._ouvrir(body.get("tg2bd"), ts)
        elif not night and self._open_id is not None:
            self._fermer(body.get("tendb"), ts)
        # night True déjà ouverte, ou False sans ouverture : rien (double appui filtré, ou repos)

    # ---- gestes relayés (incrément 4) ---------------------------------------------------
    def current(self) -> dict | None:
        """La nuit qu'un « je me couche » retrouverait : ouverte, ou retenue en attente du réveil."""
        if self._open_id is not None:
            return self.store.get_night(self._open_id)
        return self.store.get_pending_device_night()

    def closable(self) -> dict | None:
        """La nuit qu'un « je me lève » clôt : la courante, ou une que le firmware a close et dont
        le lever attend encore la fin de l'alarme."""
        nuit = self.current()
        if nuit is None and self._awaiting_id is not None:
            nuit = self.store.get_night(self._awaiting_id)
        return nuit

    def bedtime_taken(self, ts: float, raw_tg2bd: str | None) -> int:
        """Notre `{"night": true}` a été relu dans `wungt` : coucher = l'heure de l'APPUI, confirmé.

        Si la collecte a vu la transition avant notre relecture, la nuit qu'elle vient d'ouvrir est
        la nôtre — le relais a vérifié qu'il n'y en avait aucune avant l'appui : elle reçoit
        l'heure de l'appui."""
        if self._open_id is None:
            self._adopter(self.store.create_night(_jour(ts), bedtime=ts, raw_tg2bd=raw_tg2bd,
                                                  origin="confirmed"))
        else:
            self.store.night_confirm_bedtime(self._open_id, _jour(ts), ts)
        _LOGGER.info("nuit %s ouverte (appui relayé)", self._open_id)
        return self._open_id

    def bedtime_pending(self, ts: float) -> int:
        """Réveil injoignable : la nuit existe dès l'appui, en attente du réveil (cadrage §5)."""
        nid = self.store.create_night(_jour(ts), bedtime=ts, raw_tg2bd=None,
                                      state="pending_device", origin="confirmed")
        self.store.add_pending_gesture("bedtime", ts, nid)
        _LOGGER.info("nuit %s retenue : coucher en attente du réveil", nid)
        return nid

    def pending_taken(self, night_id: int, raw_tg2bd: str | None) -> None:
        """Le coucher retenu a été relu dans le réveil : la nuit s'ouvre. Son heure reste celle de
        l'appui ; `tg2bd`, daté du retour, n'est gardé que pour la trace."""
        self.store.night_opened(night_id, raw_tg2bd)
        self._adopter(night_id)
        _LOGGER.info("nuit %s : coucher retenu pris par le réveil", night_id)

    def pending_refused(self, night_id: int) -> None:
        """Le réveil refuse la session : la nuit sort de l'attente, anormale, son coucher gardé.
        Laissée en attente, elle bloquerait tout coucher suivant."""
        self.store.night_abnormal(night_id, None)
        _LOGGER.warning("nuit %s anormale : le réveil a refusé le coucher retenu", night_id)

    def rise_confirmed(self, night_id: int, ts: float) -> None:
        """« Je me lève » : lever = l'heure de l'appui, confirmé ; la nuit est close. Un coucher
        encore retenu pour elle n'a plus rien à ouvrir."""
        self.store.night_rise_confirmed(night_id, ts)
        self.store.settle_night_gestures(night_id, "bedtime")
        if self._open_id == night_id:
            self._open_id = None
        if self._awaiting_id == night_id:
            self._awaiting_id = None
        _LOGGER.info("nuit %s close (lever relayé)", night_id)

    # ---- transitions (observation) ------------------------------------------------------
    def _ouvrir(self, tg2bd, ts: float) -> None:
        retenue = self.store.get_pending_device_night()
        if retenue is not None:
            # un appui retenu attendait le réveil, et une session s'y est ouverte : c'est la
            # sienne. L'heure reste celle de l'appui ; le geste en attente devient sans objet.
            self.store.night_opened(retenue["id"], tg2bd)
            self.store.settle_night_gestures(retenue["id"], "bedtime")
            self._adopter(retenue["id"])
            _LOGGER.info("nuit %s : session observée, coucher retenu adopté", retenue["id"])
            return
        self._adopter(self.store.create_night(_jour(ts), bedtime=ts, raw_tg2bd=tg2bd))
        _LOGGER.info("nuit %s ouverte (coucher observé)", self._open_id)

    def _adopter(self, night_id: int) -> None:
        self._open_id = night_id
        self._alarme_depuis_ouverture = self._alarme_active   # déjà en alarme au coucher ? rare
        self._alarme_tombee_a = None

    def _fermer(self, tendb, ts: float) -> None:
        seq = self._open_id
        self._open_id = None
        if not self._alarme_depuis_ouverture:
            self.store.night_abnormal(seq, tendb)         # expiration, sans alarme : pas de lever
            _LOGGER.warning("nuit %s anormale : close sans alarme (expiration)", seq)
            return
        if self._alarme_active:
            # l'alarme sonne encore : la session est close, le lever attend la chute du bit 11
            self.store.night_awaiting_rise(seq, tendb)
            self._awaiting_id = seq
        else:
            # l'alarme est déjà finie : lever = l'instant où le bit 11 est retombé
            self.store.night_close(seq, risetime=self._alarme_tombee_a or ts, raw_tendb=tendb)
