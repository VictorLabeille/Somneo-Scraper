"""La sauvegarde : copie cohérente valide, rotation 7 quotidiennes / 4 hebdomadaires, plafond."""
from __future__ import annotations

import gzip
import sqlite3

from somneo_collector import backup
from somneo_collector.config import Config
from somneo_collector.store import Store


def _jour(base: float, n: int) -> float:
    return base + n * 86400.0


def test_sauvegarde_valide_et_lisible(tmp_path):
    store = Store(tmp_path / "somneo.db")
    store.add_reading({"mstmp": 21.0}, ts=1000.0)
    store.close()
    dest = tmp_path / "backups"
    f = backup.sauvegarder(str(tmp_path / "somneo.db"), dest, Config(), maintenant=1_700_000_000)
    # une sauvegarde est un sqlite gzippé, relisible, qui contient la donnée
    with gzip.open(f, "rb") as fh:
        (tmp_path / "restored.db").write_bytes(fh.read())
    conn = sqlite3.connect(tmp_path / "restored.db")
    assert conn.execute("SELECT mstmp FROM reading").fetchone()[0] == 21.0
    conn.close()


def test_rotation_garde_sept_quotidiennes(tmp_path):
    Store(tmp_path / "s.db").close()
    dest = tmp_path / "b"
    base = 1_700_000_000
    for n in range(10):                       # dix jours d'affilée
        backup.sauvegarder(str(tmp_path / "s.db"), dest, Config(), maintenant=_jour(base, n))
    daily = sorted(dest.glob("daily-*.db.gz"))
    assert len(daily) == 7                    # les 7 plus récentes
    assert len(list(dest.glob("weekly-*.db.gz"))) >= 1


def test_plafond_elague(tmp_path):
    Store(tmp_path / "s.db").close()
    dest = tmp_path / "b"
    cfg = Config()
    petit = cfg.sauvegarde.__class__(quotidiennes=7, hebdomadaires=4, plafond_octets=1)
    cfg = Config(sauvegarde=petit)            # plafond ridicule : ne garde qu'un fichier
    base = 1_700_000_000
    for n in range(3):
        backup.sauvegarder(str(tmp_path / "s.db"), dest, cfg, maintenant=_jour(base, n))
    assert len(list(dest.glob("*.db.gz"))) == 1
