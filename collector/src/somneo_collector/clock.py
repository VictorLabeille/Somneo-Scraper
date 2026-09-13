"""Surveillance de l'horloge du réveil — lecture seule (plan §6).

Le collecteur ne remet PAS l'heure : elle ne s'écrit pas (§2.E, décision du 2026-09-13). Il
mesure la dérive et la journalise. Chaque contrôle enregistre l'heure de la carte, celle du
port `time` et celle de `wutim` (l'horloge décomposée qui date les nuits), et leurs écarts.

La bascule saisonnière (25 octobre 2026) est le seul saut d'une heure : ce jour-là, entre 1 h
et 5 h locales, le contrôle passe à la minute (§6). C'est en lecture seule et n'attend rien —
même réveil connecté, on capte le comportement de référence.
"""
from __future__ import annotations

import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from .gateway import DeviceGateway

TZ = ZoneInfo("Europe/Paris")


def _epoch_time(body: dict | None) -> float | None:
    """Port `time` → epoch, depuis `datetime` ISO 8601 avec décalage."""
    if not isinstance(body, dict):
        return None
    try:
        return datetime.fromisoformat(body["datetime"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


def _epoch_wutim(body: dict | None, tzinfo=TZ) -> float | None:
    """Port `wutim` (année, mois, jour, heure, min, sec décomposés) → epoch, en heure locale."""
    if not isinstance(body, dict):
        return None
    try:
        return datetime(body["yrltm"], body["moltm"], body["dtltm"],
                        body["hrltm"], body["miltm"], body["scltm"], tzinfo=tzinfo).timestamp()
    except (KeyError, TypeError, ValueError):
        return None


async def mesurer(gw: DeviceGateway) -> tuple[float, float | None, float | None]:
    """Renvoie (heure carte, heure `time`, heure `wutim`). Les deux dernières None si illisibles.

    L'heure de la carte est prise entre les deux lectures : c'est le meilleur repère commun."""
    t = await gw.read("time", produit=0)
    carte = time.time()
    w = await gw.read("wutim")
    return carte, _epoch_time(t.corps), _epoch_wutim(w.corps)


def en_fenetre_bascule(cfg_horloge, maintenant: float | None = None) -> bool:
    """Vrai le jour de la bascule, entre l'heure de début et de fin locales : contrôle à la minute."""
    dt = datetime.fromtimestamp(maintenant if maintenant is not None else time.time(), TZ)
    try:
        jour = date.fromisoformat(cfg_horloge.bascule_dst_iso)
    except ValueError:
        return False
    return dt.date() == jour and cfg_horloge.bascule_debut_h <= dt.hour < cfg_horloge.bascule_fin_h
