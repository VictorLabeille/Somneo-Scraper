"""Configuration du collecteur : cadences, chemins, seuils.

Les valeurs par défaut sont celles tranchées au plan technique (§4 cadences, §6 horloge,
§3 sauvegarde). Un fichier TOML sur la carte (`/etc/somneo-collector/config.toml`, hors dépôt)
les remplace champ par champ — il ne vit pas ici pour ne pas figer dans le dépôt public des
détails propres à l'installation. La lecture TOML est celle de la bibliothèque standard
(`tomllib`, Python ≥ 3.11) : aucune dépendance de plus.
"""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
from pathlib import Path

# La variable d'env permet de pointer un autre TOML (essais hors carte) sans toucher au code.
CHEMIN_CONFIG_DEFAUT = Path(os.environ.get("SOMNEO_COLLECTOR_CONFIG",
                                           "/etc/somneo-collector/config.toml"))


@dataclass(frozen=True)
class Cadences:
    """Périodes de collecte, en secondes (plan §4). Le palier de `wusrd` démarre au plus lent."""

    wusrd: float = 60.0            # puis paliers 30 → 15 s, un palier après 24 h sans 500
    wusts: float = 10.0            # état : alarme, rappel, veille
    wungt: float = 30.0            # suivi de nuit
    dataupload: float = 300.0      # agrégats de fenêtre (moyenne, min, max, histogrammes)
    reglages: float = 60.0         # wulgt, wudsk, wualm/*, wuply — le miroir
    profils: float = 86400.0       # les 16 profils d'alarme, une fois par jour
    horloge: float = 3600.0        # time, wutim, wutms — la dérive (§6)
    liaison: float = 3600.0        # backend, transport, device — l'isolement, dans la durée
    fichiers: float = 86400.0      # files/* — noms des thèmes et sons


@dataclass(frozen=True)
class Horloge:
    """Surveillance de l'horloge (§6). La correction n'existe pas : l'heure ne s'écrit pas."""

    seuil_ecart_s: float = 10.0            # au-delà, on SIGNALE (jamais on corrige)
    bascule_dst_iso: str = "2026-10-25"    # jour de la prochaine bascule saisonnière
    bascule_debut_h: int = 1               # instrumentation minute par minute de 1 h…
    bascule_fin_h: int = 5                 # …à 5 h, heure locale, ce jour-là (§6)


@dataclass(frozen=True)
class Sauvegarde:
    """Rotation des sauvegardes (§3, §11 point 7)."""

    quotidiennes: int = 7
    hebdomadaires: int = 4
    plafond_octets: int = 1_500_000_000    # 1,5 Go, compressé


@dataclass(frozen=True)
class Config:
    base: Path = Path("/var/lib/somneo-collector/somneo.db")
    sauvegardes: Path = Path("/var/lib/somneo-collector/backups")
    hote_force: str | None = None          # adresse imposée (tests, faux réveil) ; sinon SSDP
    espacement_s: float = 0.2              # entre deux requêtes au réveil (prudence, §7 doc)
    # Le drapeau que `systemd-timesyncd` pose à la synchronisation NTP : la collecte l'attend avant
    # de dater quoi que ce soit, car la carte repart en retard après une coupure (écart 5,
    # docs/radxa.md). None : pas d'attente (tests). `attente_synchro_s = 0` fait de même en TOML.
    synchro_ntp: Path | None = Path("/run/systemd/timesync/synchronized")
    attente_synchro_s: float = 300.0       # passé ce délai, la collecte part, et le statut le dit
    api_host: str = "0.0.0.0"
    api_port: int = 8760
    mdns_service: str = "_somneo-scraper._tcp"   # contrat SleepMaxxer, ≤ 15 o (RFC 6763)
    mdns_txt_api: str = "v1"
    cadences: Cadences = field(default_factory=Cadences)
    horloge: Horloge = field(default_factory=Horloge)
    sauvegarde: Sauvegarde = field(default_factory=Sauvegarde)


def _fusion(base, table: dict):
    """Applique les clés d'une table TOML sur une dataclass, en descendant dans les sous-tables."""
    champs = {f.name for f in base.__dataclass_fields__.values()}
    modifs = {}
    for cle, valeur in table.items():
        if cle not in champs:
            raise ValueError(f"clé de configuration inconnue : {cle}")
        courant = getattr(base, cle)
        if hasattr(courant, "__dataclass_fields__") and isinstance(valeur, dict):
            modifs[cle] = _fusion(courant, valeur)
        elif isinstance(courant, Path):
            modifs[cle] = Path(valeur)
        else:
            modifs[cle] = valeur
    return replace(base, **modifs)


def charger(chemin: Path | None = None) -> Config:
    """Config par défaut, écrasée par le TOML s'il existe. Absent = les défauts, sans erreur."""
    chemin = chemin or CHEMIN_CONFIG_DEFAUT
    cfg = Config()
    if chemin.exists():
        with open(chemin, "rb") as fh:
            cfg = _fusion(cfg, tomllib.load(fh))
    return cfg
