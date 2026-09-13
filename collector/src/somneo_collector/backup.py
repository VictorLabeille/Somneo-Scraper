"""Sauvegarde en rotation (plan §3, §11 point 7) : 7 quotidiennes, 4 hebdomadaires, compressées.

Copie cohérente par `VACUUM INTO` (sûr même en WAL et pendant l'écriture), puis gzip. Rotation :
on garde les N quotidiennes et M hebdomadaires les plus récentes ; une hebdomadaire est figée à
la première sauvegarde de chaque semaine ISO. Un plafond global (1,5 Go) élague les plus
anciennes en cas de dépassement.
"""
from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime
from pathlib import Path

_LOGGER = logging.getLogger(__name__)


def _copie_compressee(db_path: str, cible: Path) -> None:
    """Copie cohérente de la base vers `cible` (.gz). VACUUM INTO puis gzip, temp nettoyé."""
    with tempfile.TemporaryDirectory() as d:
        brut = Path(d) / "snap.db"
        conn = sqlite3.connect(db_path, timeout=30.0)
        try:
            conn.execute("VACUUM INTO ?", (str(brut),))
        finally:
            conn.close()
        with open(brut, "rb") as src, gzip.open(cible, "wb") as dst:
            shutil.copyfileobj(src, dst)


def _garder_recentes(dossier: Path, prefixe: str, combien: int) -> None:
    fichiers = sorted(dossier.glob(f"{prefixe}-*.db.gz"))
    for vieux in fichiers[:-combien] if combien > 0 else fichiers:
        vieux.unlink(missing_ok=True)


def _sous_plafond(dossier: Path, plafond: int) -> None:
    """Tant que le total dépasse le plafond, supprime la sauvegarde la plus ancienne."""
    fichiers = sorted(dossier.glob("*.db.gz"), key=lambda p: p.stat().st_mtime)
    total = sum(p.stat().st_size for p in fichiers)
    while total > plafond and len(fichiers) > 1:
        vieux = fichiers.pop(0)
        total -= vieux.stat().st_size
        vieux.unlink(missing_ok=True)
        _LOGGER.warning("plafond de sauvegarde dépassé : %s supprimée", vieux.name)


def sauvegarder(db_path: str, dossier: Path, cfg, maintenant: float | None = None) -> Path:
    """Fait la sauvegarde du jour, fige l'hebdomadaire si besoin, élague. Rend le fichier du jour."""
    dossier = Path(dossier)
    dossier.mkdir(parents=True, exist_ok=True)
    dt = datetime.fromtimestamp(maintenant if maintenant is not None else time.time())
    quotidienne = dossier / f"daily-{dt:%Y%m%d}.db.gz"
    _copie_compressee(db_path, quotidienne)

    annee, semaine, _ = dt.isocalendar()
    hebdo = dossier / f"weekly-{annee:04d}{semaine:02d}.db.gz"
    if not hebdo.exists():
        shutil.copyfile(quotidienne, hebdo)

    _garder_recentes(dossier, "daily", cfg.sauvegarde.quotidiennes)
    _garder_recentes(dossier, "weekly", cfg.sauvegarde.hebdomadaires)
    _sous_plafond(dossier, cfg.sauvegarde.plafond_octets)
    return quotidienne
