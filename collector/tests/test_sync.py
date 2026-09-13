"""Rattrapage (§7) et catalogue : /v1/sync (since_seq et before) et /v1/catalog/themes."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.config import Config
from somneo_collector.nights import NightTracker
from somneo_collector.store import Store


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "s.db")
    yield TestClient(create_app(store, Config())), store


def test_sync_since_seq_rend_les_changements_dans_l_ordre(client):
    c, store = client
    store.add_reading({"mstmp": 20.0}, ts=100.0)
    marque = store.seq_courant()
    store.add_reading({"mstmp": 21.0}, ts=200.0)
    store.open_outage("réveil injoignable", ts=250.0)
    r = c.get("/v1/sync", params={"since_seq": marque}).json()
    assert r["mode"] == "since_seq" and r["current_seq"] >= marque
    kinds = [it["kind"] for it in r["items"]]
    assert "reading" in kinds and "outage" in kinds
    assert all(it["seq"] > marque for it in r["items"])            # rien d'avant la marque
    assert [it["seq"] for it in r["items"]] == sorted(it["seq"] for it in r["items"])  # dans l'ordre


def test_sync_before_rend_les_nuits_avec_leurs_points(client):
    c, store = client
    t = NightTracker(store)
    t.on_wungt({"night": True, "tg2bd": "", "tendb": ""}, ts=1000.0)
    for i in range(3):
        store.add_reading({"mstmp": 20.0 + i}, ts=1000.0 + i)
    t.on_wusts(1 << 11, ts=1100.0)
    t.on_wungt({"night": False, "tg2bd": "", "tendb": ""}, ts=1110.0)
    t.on_wusts(1, ts=1200.0)
    r = c.get("/v1/sync", params={"before": 1e12}).json()
    assert r["mode"] == "before" and r["count"] == 1
    assert len(r["nights"][0]["readings"]) == 3


def test_sync_exige_un_parametre(client):
    c, _ = client
    assert c.get("/v1/sync").status_code == 422


def test_catalog_themes_depuis_les_ports_files(client):
    c, store = client
    store.record_port_change("files/wakeup", {"1": "Sunny day", "2": "Forest Birds"})
    cat = c.get("/v1/catalog/themes").json()["catalog"]
    assert cat["wakeup"]["source"] == "appareil"
    assert cat["wakeup"]["themes"]["1"] == "Sunny day"
