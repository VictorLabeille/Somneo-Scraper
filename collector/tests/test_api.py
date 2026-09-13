"""L'API : /v1/status dérivé de la base, /v1/readings, et le champ served_at partout."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.config import Config
from somneo_collector.state import RuntimeState
from somneo_collector.store import Store


@pytest.fixture
def client(tmp_path):
    store = Store(tmp_path / "somneo.db")
    state = RuntimeState(reveil_host="192.0.2.10:443")
    app = create_app(store, Config(), state)
    yield TestClient(app), store


def test_status_reveil_joignable_et_horloge(client):
    c, store = client
    store.add_reading({"mstmp": 20.0}, ts=1000.0)
    store.add_clock_check(card_time=1000.0, device_time=1012.0, wutim_time=1009.0, ts=1000.0)
    store.record_port_change("backend", {"dcs-state": "subscribed", "lastsignon": "X"})
    r = c.get("/v1/status").json()
    assert "served_at" in r
    assert r["reveil"]["joignable"] is True
    assert r["horloge"]["ecart_s"] == 12.0 and r["horloge"]["au_dela_du_seuil"] is True
    assert "ne s'écrit pas" in r["horloge"]["correction"]
    assert r["cloud"]["dcs_state"] == "subscribed"
    assert r["disque"]["total"] > 0


def test_status_indisponibilite(client):
    c, store = client
    store.open_outage("réveil injoignable", ts=500.0)
    r = c.get("/v1/status").json()
    assert r["reveil"]["joignable"] is False
    assert r["reveil"]["cause_indisponibilite"] == "réveil injoignable"


def test_readings_fenetre(client):
    c, store = client
    for i in range(5):
        store.add_reading({"mslux": i}, ts=100.0 + i)
    r = c.get("/v1/readings", params={"from": 101.0, "to": 103.0}).json()
    assert r["count"] == 3 and [x["mslux"] for x in r["readings"]] == [1, 2, 3]
    assert r["served_at"] <= time.time() + 1
