"""La base : un écrivain, des lecteurs concurrents (WAL). Plan §3.

`Store` détient l'unique connexion d'écriture (la boucle de collecte et les commandes,
sérialisées en amont par la passerelle). Les lectures de l'API ouvrent chacune une connexion
courte en lecture seule — c'est le cas d'usage de WAL. Toute ligne exposée porte un `seq`
global, alloué par `meta.next_seq`.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from importlib import resources
from pathlib import Path
from typing import Any

CHAMPS_READING = ("mslux", "mstmp", "msrhu", "mssnd", "avlux", "avtmp", "avrhu", "avsnd")


def _pragmas(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = FULL")     # coupure de courant en écriture = mode probable
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row


class Store:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # Un seul écrivain LOGIQUE (plan §3), mais deux threads l'appellent : la boucle de
        # collecte et le threadpool des routes de l'API (une correction est une écriture). D'où
        # check_same_thread=False + un verrou qui SÉRIALISE toutes les écritures : jamais deux à
        # la fois, la garantie du plan tenue à travers les threads.
        self._wlock = threading.Lock()
        self._w = sqlite3.connect(self.path, timeout=30.0, check_same_thread=False)
        _pragmas(self._w)
        schema = resources.files("somneo_collector.store").joinpath("schema.sql").read_text()
        with self._wlock, self._w:
            self._w.executescript(schema)
        self._dernier_corps: dict[str, str] = {}     # port -> dernier body JSON, pour « au changement »

    # ---- allocation du seq global -------------------------------------------------------
    def _next_seq(self) -> int:
        row = self._w.execute("SELECT value FROM meta WHERE key='next_seq'").fetchone()
        seq = row[0]
        self._w.execute("UPDATE meta SET value=? WHERE key='next_seq'", (seq + 1,))
        return seq

    def seq_courant(self) -> int:
        with self._wlock:
            return self._w.execute("SELECT value FROM meta WHERE key='next_seq'").fetchone()[0] - 1

    # ---- écritures ----------------------------------------------------------------------
    def add_reading(self, body: dict, ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            seq = self._next_seq()
            vals = [body.get(c) for c in CHAMPS_READING]
            self._w.execute(
                f"INSERT INTO reading (seq, ts, {', '.join(CHAMPS_READING)}) "
                f"VALUES (?, ?, {', '.join('?' * len(CHAMPS_READING))})",
                [seq, ts, *vals])
        return seq

    def add_window_aggregate(self, kind: str, avg, lo, hi, hist, ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            seq = self._next_seq()
            self._w.execute(
                "INSERT INTO window_aggregate (seq, ts, kind, avg, lo, hi, hist) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [seq, ts, kind, avg, lo, hi, json.dumps(hist) if hist is not None else None])
        return seq

    def record_port_change(self, port: str, body: Any, ts: float | None = None) -> int | None:
        """Insère le corps SEULEMENT s'il diffère du dernier vu pour ce port. Rend le seq, ou None.

        Tout se fait sous le verrou en une prise (le verrou n'est pas réentrant) : la relecture
        du dernier corps après un redémarrage, la comparaison, et l'insertion."""
        encode = json.dumps(body, sort_keys=True, ensure_ascii=False)
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            if port not in self._dernier_corps:
                row = self._w.execute(
                    "SELECT body FROM port_change WHERE port=? ORDER BY seq DESC LIMIT 1",
                    (port,)).fetchone()
                if row is not None:
                    self._dernier_corps[port] = row[0]
            if self._dernier_corps.get(port) == encode:
                return None
            seq = self._next_seq()
            self._w.execute("INSERT INTO port_change (seq, ts, port, body) VALUES (?, ?, ?, ?)",
                            [seq, ts, port, encode])
            self._dernier_corps[port] = encode
        return seq

    def see_device(self, serial: str, model: str | None, firmware: str | None,
                   ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            row = self._w.execute("SELECT id FROM device WHERE serial=?", (serial,)).fetchone()
            if row is None:
                seq = self._next_seq()
                self._w.execute(
                    "INSERT INTO device (serial, model, firmware, first_seen, last_seen, seq) "
                    "VALUES (?, ?, ?, ?, ?, ?)", [serial, model, firmware, ts, ts, seq])
            else:
                self._w.execute("UPDATE device SET last_seen=?, model=?, firmware=? WHERE id=?",
                                [ts, model, firmware, row[0]])

    def heartbeat(self, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            self._w.execute("INSERT INTO heartbeat (id, ts) VALUES (1, ?) "
                            "ON CONFLICT(id) DO UPDATE SET ts=excluded.ts", (ts,))

    def add_clock_check(self, card_time, device_time, wutim_time,
                        ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        off_t = round(device_time - card_time, 3) if (device_time and card_time) else None
        off_w = round(wutim_time - card_time, 3) if (wutim_time and card_time) else None
        with self._wlock, self._w:
            seq = self._next_seq()
            self._w.execute(
                "INSERT INTO clock_check (seq, ts, card_time, device_time, wutim_time, "
                "offset_time_s, offset_wutim_s, corrected) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                [seq, ts, card_time, device_time, wutim_time, off_t, off_w])
        return seq

    def open_outage(self, cause: str, ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            seq = self._next_seq()
            cur = self._w.execute(
                "INSERT INTO outage (seq, start, cause, failures) VALUES (?, ?, ?, 0)",
                [seq, ts, cause])
        return cur.lastrowid

    def bump_outage(self, outage_id: int) -> None:
        with self._wlock, self._w:
            self._w.execute("UPDATE outage SET failures = failures + 1 WHERE id=?", (outage_id,))

    def close_outage(self, outage_id: int, ts: float | None = None) -> None:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            self._w.execute("UPDATE outage SET end=?, seq=? WHERE id=?",
                            [ts, self._next_seq(), outage_id])

    # ---- nuits (incrément 2) ------------------------------------------------------------
    def create_night(self, day: str, bedtime: float, raw_tg2bd: str | None,
                     ts: float | None = None) -> int:
        """Ouvre une nuit ; rend son `id` STABLE. `bedtime` en heure COLLECTEUR (NTP), pas tg2bd."""
        with self._wlock, self._w:
            cur = self._w.execute(
                "INSERT INTO night (seq, day, bedtime, state, bedtime_origin, raw_tg2bd) "
                "VALUES (?, ?, ?, 'open', 'observed', ?)",
                [self._next_seq(), day, bedtime, raw_tg2bd])
        return cur.lastrowid

    def night_awaiting_rise(self, night_id: int, raw_tendb: str | None) -> None:
        """La session est close côté `wungt`, mais le lever attend la fin de l'alarme (bit 11)."""
        with self._wlock, self._w:
            self._w.execute(
                "UPDATE night SET state='closed', risetime=NULL, risetime_origin='estimated', "
                "raw_tendb=?, seq=? WHERE id=?", [raw_tendb, self._next_seq(), night_id])

    def night_set_rise(self, night_id: int, risetime: float) -> None:
        with self._wlock, self._w:
            self._w.execute(
                "UPDATE night SET risetime=?, state='closed', risetime_origin='estimated', seq=? "
                "WHERE id=?", [risetime, self._next_seq(), night_id])

    def night_close(self, night_id: int, risetime: float, raw_tendb: str | None) -> None:
        """Ferme d'un coup avec un lever connu (alarme déjà finie à la clôture)."""
        with self._wlock, self._w:
            self._w.execute(
                "UPDATE night SET risetime=?, state='closed', risetime_origin='estimated', "
                "raw_tendb=?, seq=? WHERE id=?",
                [risetime, raw_tendb, self._next_seq(), night_id])

    def night_abnormal(self, night_id: int, raw_tendb: str | None) -> None:
        """Nuit close sans alarme (expiration à 12 h) : pas d'heure de lever, signalée."""
        with self._wlock, self._w:
            self._w.execute(
                "UPDATE night SET state='abnormal', raw_tendb=?, seq=? WHERE id=?",
                [raw_tendb, self._next_seq(), night_id])

    def get_open_night(self) -> dict | None:
        with self._wlock:
            row = self._w.execute("SELECT * FROM night WHERE state='open' "
                                  "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def get_awaiting_night(self) -> dict | None:
        with self._wlock:
            row = self._w.execute("SELECT * FROM night WHERE state='closed' AND risetime IS NULL "
                                  "ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def add_night_correction(self, night_id: int, field: str, value: float,
                             ts: float | None = None) -> int:
        ts = ts if ts is not None else time.time()
        with self._wlock, self._w:
            seq = self._next_seq()
            self._w.execute(
                "INSERT INTO night_correction (seq, night_id, ts, field, value) "
                "VALUES (?, ?, ?, ?, ?)", [seq, night_id, ts, field, value])
            # la correction fait foi : la valeur servie de la nuit suit, sa valeur relevée reste
            if field in ("bedtime", "risetime"):
                self._w.execute(f"UPDATE night SET {field}=?, seq=? WHERE id=?",
                                [value, self._next_seq(), night_id])
        return seq

    def list_nights(self, debut: float, fin: float) -> list[dict]:
        conn = self._ro()
        try:
            rows = conn.execute(
                "SELECT * FROM night WHERE bedtime >= ? AND bedtime <= ? ORDER BY bedtime",
                (debut, fin)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def get_night(self, night_id: int) -> dict | None:
        conn = self._ro()
        try:
            row = conn.execute("SELECT * FROM night WHERE id=?", (night_id,)).fetchone()
            if not row:
                return None
            night = dict(row)
            night["corrections"] = [dict(c) for c in conn.execute(
                "SELECT * FROM night_correction WHERE night_id=? ORDER BY seq", (night_id,))]
            return night
        finally:
            conn.close()

    def close(self) -> None:
        self._w.close()

    # ---- lectures (API) : connexion courte, lecture seule -------------------------------
    def _ro(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def latest_reading(self) -> dict | None:
        conn = self._ro()
        try:
            row = conn.execute("SELECT * FROM reading ORDER BY seq DESC LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def readings_between(self, debut: float, fin: float, limite: int = 100000) -> list[dict]:
        conn = self._ro()
        try:
            rows = conn.execute(
                "SELECT * FROM reading WHERE ts >= ? AND ts <= ? ORDER BY ts LIMIT ?",
                (debut, fin, limite)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def latest_clock_check(self) -> dict | None:
        conn = self._ro()
        try:
            row = conn.execute("SELECT * FROM clock_check ORDER BY seq DESC LIMIT 1").fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def last_heartbeat(self) -> float | None:
        conn = self._ro()
        try:
            row = conn.execute("SELECT ts FROM heartbeat WHERE id=1").fetchone()
            return row[0] if row else None
        finally:
            conn.close()

    def open_outages(self) -> list[dict]:
        conn = self._ro()
        try:
            rows = conn.execute("SELECT * FROM outage WHERE end IS NULL ORDER BY start").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def recent_outages(self, depuis: float) -> list[dict]:
        conn = self._ro()
        try:
            rows = conn.execute(
                "SELECT * FROM outage WHERE start >= ? OR end IS NULL ORDER BY start DESC",
                (depuis,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def last_port_body(self, port: str) -> dict | None:
        conn = self._ro()
        try:
            row = conn.execute(
                "SELECT body FROM port_change WHERE port=? ORDER BY seq DESC LIMIT 1",
                (port,)).fetchone()
            return json.loads(row[0]) if row else None
        finally:
            conn.close()
