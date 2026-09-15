"""L'indisponibilité en cours : une seule à la fois, tenue au-dessus des redécouvertes.

Le superviseur crée ce suivi une fois, comme la machine des nuits, et le passe à chaque
`Collector` ; la redécouverte s'en sert aussi. Il remplace l'id que chaque `Collector` gardait
pour lui : le superviseur en recrée un à chaque redécouverte, le nouveau ne fermait donc rien, et
`gateway()`, qui lisait la base, refusait tout pilotage alors que le réveil répondait (écart 10,
`.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`, tranché le 2026-09-15) :

- une seule indisponibilité ouverte à la fois. La même cause incrémente son compteur ; une autre
  la ferme et en ouvre une au même instant. Deux trous ne se chevauchent jamais, chacun a une
  cause ;
- seul un relevé réussi la ferme, et jamais avant son début : l'horloge de la carte peut revenir
  en arrière au démarrage (docs/radxa.md) ;
- au démarrage, avant le premier battement, les lignes laissées ouvertes par le processus
  précédent sont fermées (`repair_stale`), puis « collecteur arrêté » s'ouvre au dernier signe
  de vie de ce processus (`open_stopped`, écart 5).
"""
from __future__ import annotations

import logging
import time

from .store import Store

_LOGGER = logging.getLogger(__name__)
CAUSE_ARRET = "collecteur arrêté"


class OutageTracker:
    def __init__(self, store: Store) -> None:
        self.store = store
        self._id: int | None = None
        self._cause: str | None = None
        self._debut: float | None = None

    @property
    def current(self) -> int | None:
        """L'id de l'indisponibilité en cours, ou None si le réveil répond."""
        return self._id

    def report(self, cause: str, ts: float | None = None) -> bool:
        """Un échec. Rend True si une indisponibilité vient de s'ouvrir."""
        if self._id is not None and cause == self._cause:
            self.store.bump_outage(self._id)
            return False
        ts = ts if ts is not None else time.time()
        if self._id is not None:                 # autre cause : l'une finit où l'autre commence
            ts = max(ts, self._debut)
            self.store.close_outage(self._id, ts=ts)
        self._id, self._cause, self._debut = self.store.open_outage(cause, ts=ts), cause, ts
        return True

    def recover(self, ts: float | None = None) -> bool:
        """Un relevé réussi. Rend True si une indisponibilité vient de se fermer."""
        if self._id is None:
            return False
        ts = ts if ts is not None else time.time()
        self.store.close_outage(self._id, ts=max(ts, self._debut))
        self._id = self._cause = self._debut = None
        return True

    def open_stopped(self) -> int | None:
        """Ouvre « collecteur arrêté » (écart 5, tranché le 2026-09-15) ; rend son id, ou None.

        À appeler au démarrage, après `repair_stale` et avant le premier battement. La ligne part
        du dernier signe de vie du processus précédent, le plus tardif de son dernier battement et
        de son dernier relevé (cadrage backend §3.B). Le premier relevé réussi la fermera ; un
        échec la remplacera par sa cause, au même instant. Aucun seuil : chaque arrêt a sa ligne.
        Sans battement ni relevé (base neuve), rien : il n'y avait pas de collecteur."""
        dernier = self.store.latest_reading()
        vie = [t for t in (self.store.last_heartbeat(), dernier["ts"] if dernier else None)
               if t is not None]
        if not vie:
            return None
        self.report(CAUSE_ARRET, ts=max(vie))
        return self._id

    def repair_stale(self) -> list[int]:
        """Ferme les indisponibilités laissées ouvertes par un processus précédent ; rend leurs ids.

        À appeler au démarrage, **avant le premier battement** : le dernier battement en base est
        alors celui du processus précédent. Chaque ligne est fermée au plus tôt de deux bornes
        postérieures à son début : le premier relevé qui la suit (le réveil répondait de nouveau,
        à la cadence de `wusrd` près) et le dernier battement (le processus vivait encore). Sans
        l'une ni l'autre, la ligne est fermée à son début. `close_outage` reprend le `seq` : le
        rattrapage voit la fermeture."""
        battement = self.store.last_heartbeat()
        fermees = []
        for ligne in self.store.open_outages():
            if ligne["id"] == self._id:
                continue
            debut = ligne["start"]
            bornes = [t for t in (self.store.first_reading_after(debut), battement)
                      if t is not None and t >= debut]
            fin = min(bornes) if bornes else debut
            self.store.close_outage(ligne["id"], ts=fin)
            _LOGGER.warning("indisponibilité %s (%s) restée ouverte, close à %.0f",
                            ligne["id"], ligne["cause"], fin)
            fermees.append(ligne["id"])
        return fermees
