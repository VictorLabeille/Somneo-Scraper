"""Machine à états des sessions de nuit (plan §5, règles des quatre matins d'alarme mesurés).

Incrément 2 : **en lecture seule**. Les gestes sont faits par SleepMapper ; on observe `wungt`
et `wusts`, on ne provoque rien. Les gestes émis par le collecteur (bouton de l'app) viennent à
l'incrément 4.

**Les heures sont dans le référentiel du collecteur (NTP)** — contrat §F : le coucher est
l'instant où on OBSERVE `wungt` passer à `night: true` (à ~30 s près), pas la date `tg2bd`
inscrite par le réveil sur son horloge `wutim`. `tg2bd`/`tendb` sont conservés bruts pour la
traçabilité, jamais servis comme l'heure.

Faits qui commandent la machine (`docs/somneo-api.md` §4) :
- C'est le firmware qui clôt la nuit, à l'heure programmée de l'alarme, avant tout geste :
  `wungt` repasse à `night: false` seul. On ne peut donc pas attendre un geste pour fermer.
- La fin de l'alarme (le lever *estimé*) arrive 1 à 15 min APRÈS la clôture de `wungt` : c'est
  la chute du bit 11 de `wusts`. La clôture et le lever sont donc décorrélés dans le temps.
- Sans alarme, `wungt` repasse à `false` par expiration (≈ 12 h) : nuit anormale, pas de lever.
- Le double appui est filtré par l'appareil (`tg2bd` ne rebouge pas) : rien à faire.
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from .store import Store

_LOGGER = logging.getLogger(__name__)
TZ = ZoneInfo("Europe/Paris")
BIT_ACTIF = 1 << 11        # `wusts` : l'appareil est en séquence de réveil/alarme


class NightTracker:
    """Consomme les observations de `wungt` et `wusts` et tient la table `night` à jour.

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

    # ---- observations -------------------------------------------------------------------
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

    # ---- transitions --------------------------------------------------------------------
    def _ouvrir(self, tg2bd, ts: float) -> None:
        jour = datetime.fromtimestamp(ts, TZ).date().isoformat()
        self._open_id = self.store.create_night(jour, bedtime=ts, raw_tg2bd=tg2bd, ts=ts)
        self._alarme_depuis_ouverture = self._alarme_active   # déjà en alarme au coucher ? rare
        self._alarme_tombee_a = None
        _LOGGER.info("nuit %s ouverte (coucher observé)", self._open_id)

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
