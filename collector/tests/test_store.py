"""La base : seq global monotone, dédup « au changement », lectures pour l'API."""
from __future__ import annotations

import pytest

from somneo_collector.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "somneo.db")
    yield s
    s.close()


def test_seq_global_monotone_entre_tables(store):
    a = store.add_reading({"mslux": 1}, ts=100.0)
    b = store.record_port_change("wusts", {"wusts": 1}, ts=101.0)
    c = store.add_reading({"mslux": 2}, ts=102.0)
    assert a < b < c                     # un seul compteur, croissant à travers les tables
    assert store.seq_courant() == c


def test_reading_8_colonnes_et_null(store):
    store.add_reading({"mslux": 5.0, "mstmp": 20.1}, ts=100.0)   # 6 grandeurs absentes
    r = store.latest_reading()
    assert r["mslux"] == 5.0 and r["mstmp"] == 20.1
    assert r["mssnd"] is None and r["avsnd"] is None


def test_port_change_au_changement(store):
    s1 = store.record_port_change("wulgt", {"onoff": False, "ltlvl": 15})
    s2 = store.record_port_change("wulgt", {"ltlvl": 15, "onoff": False})   # même contenu, ordre ≠
    s3 = store.record_port_change("wulgt", {"onoff": True, "ltlvl": 15})    # changé
    assert s1 is not None and s2 is None and s3 is not None
    assert store.last_port_body("wulgt") == {"onoff": True, "ltlvl": 15}


def test_dedup_survit_a_un_redemarrage(tmp_path):
    p = tmp_path / "s.db"
    s = Store(p)
    s.record_port_change("wusts", {"wusts": 1})
    s.close()
    s2 = Store(p)                        # cache vidé : il doit relire le dernier corps en base
    assert s2.record_port_change("wusts", {"wusts": 1}) is None
    assert s2.record_port_change("wusts", {"wusts": 2817}) is not None
    s2.close()


def test_clock_check_ecarts(store):
    store.add_clock_check(card_time=1000.0, device_time=1001.8, wutim_time=997.1, ts=1000.0)
    c = store.latest_clock_check()
    assert c["offset_time_s"] == 1.8 and c["offset_wutim_s"] == -2.9
    assert c["corrected"] == 0           # l'heure ne s'écrit pas


def test_outage_ouverte_puis_close(store):
    oid = store.open_outage("reveil injoignable", ts=200.0)
    store.bump_outage(oid)
    assert len(store.open_outages()) == 1
    store.close_outage(oid, ts=260.0)
    assert store.open_outages() == []
    row = store.recent_outages(0.0)[0]
    assert row["end"] == 260.0 and row["failures"] == 1


def test_readings_between(store):
    for i in range(5):
        store.add_reading({"mslux": i}, ts=100.0 + i)
    entre = store.readings_between(101.0, 103.0)
    assert [r["mslux"] for r in entre] == [1, 2, 3]
