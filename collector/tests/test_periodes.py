"""Routes par période (écart 8) : `GET /v1/outages` et `GET /v1/aggregates`, en lecture seule.

Promises par le plan technique (§7), tranchées « à écrire » par Victor le 2026-09-15."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.config import Config
from somneo_collector.store import Store

HIST = {"ab": [1, [0, 40, 100]], "rl": [0]}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "p.db")
    yield s
    s.close()


@pytest.fixture
def client(store):
    return TestClient(create_app(store, Config()))


# ---- indisponibilités ------------------------------------------------------------------------
def test_outages_qui_chevauchent_la_periode(store, client):
    """Une indisponibilité compte si elle chevauche la période, y compris celle encore ouverte ;
    celles qui finissent avant ou commencent après n'y sont pas."""
    avant = store.open_outage("réveil injoignable", ts=10.0)
    store.close_outage(avant, ts=20.0)
    a = store.open_outage("réveil injoignable", ts=100.0)
    store.close_outage(a, ts=200.0)
    b = store.open_outage("carte hors réseau", ts=500.0)                # encore ouverte
    apres = store.open_outage("appareil saturé", ts=900.0)
    store.close_outage(apres, ts=950.0)
    r = client.get("/v1/outages", params={"from": 150, "to": 600}).json()
    assert [o["id"] for o in r["outages"]] == [a, b] and r["count"] == 2
    assert r["outages"][1]["end"] is None and r["outages"][1]["cause"] == "carte hors réseau"
    assert (r["from"], r["to"]) == (150, 600) and "served_at" in r


def test_outages_bornes_incluses(store, client):
    a = store.open_outage("réveil injoignable", ts=100.0)
    store.close_outage(a, ts=200.0)
    assert client.get("/v1/outages", params={"from": 200, "to": 300}).json()["count"] == 1
    assert client.get("/v1/outages", params={"from": 0, "to": 100}).json()["count"] == 1


def test_outages_sans_periode_rend_tout(store, client):
    store.open_outage("réveil injoignable", ts=100.0)
    assert client.get("/v1/outages").json()["count"] == 1


# ---- agrégats --------------------------------------------------------------------------------
def test_aggregates_de_la_periode(store, client):
    store.add_window_aggregate("temp", 20.0, 19.9, 20.1, None, ts=100.0)
    store.add_window_aggregate("hum", 50.0, 49.0, 51.0, None, ts=300.0)
    store.add_window_aggregate("snd", 30, 26, 40, HIST, ts=700.0)
    r = client.get("/v1/aggregates", params={"from": 200, "to": 800}).json()
    assert [a["aggregate_kind"] for a in r["aggregates"]] == ["hum", "snd"] and r["count"] == 2
    assert json.loads(r["aggregates"][1]["hist"]) == HIST                # chaîne JSON (écart 11)


def test_periode_vide_rend_une_liste_vide(store, client):
    """Avant le début de l'historique : réponse vide explicite, jamais une erreur (cadrage §3.F)."""
    for route, cle in (("/v1/outages", "outages"), ("/v1/aggregates", "aggregates")):
        r = client.get(route, params={"from": 0, "to": 1})
        assert r.status_code == 200 and r.json()[cle] == [] and r.json()["count"] == 0
