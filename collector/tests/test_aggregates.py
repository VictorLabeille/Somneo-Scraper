"""Agrégats : leur type servi dans les deux modes du rattrapage (écart 4), et insérés au
changement (écart 11). `.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`, tranché le
2026-09-15 : `kind` reste le genre de l'élément, le type va dans `aggregate_kind` ; `hist` reste
une chaîne JSON ; un agrégat ne s'insère que s'il diffère du dernier de son type."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.collect import Collector
from somneo_collector.config import Config
from somneo_collector.gateway import DeviceGateway
from somneo_collector.nights import NightTracker
from somneo_collector.store import Store

HIST = {"ab": [1, [0, 40, 100]], "rl": [0]}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "a.db")
    yield s
    s.close()


@pytest.fixture
def client(store):
    return TestClient(create_app(store, Config()))


def _agregats(store) -> list[dict]:
    conn = store._ro()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM window_aggregate ORDER BY seq")]
    finally:
        conn.close()


def _depuis(client, seq: int = 0) -> list[dict]:
    items = client.get("/v1/sync", params={"since_seq": seq}).json()["items"]
    return [i for i in items if i["kind"] == "aggregate"]


# ---- écart 4 : le type de l'agrégat est servi -------------------------------------------------
def test_since_seq_sert_le_type_a_cote_du_genre(store, client):
    store.add_window_aggregate("temp", 20.0, 19.9, 20.1, None, ts=100.0)
    store.add_window_aggregate("snd", 30, 26, 40, HIST, ts=101.0)
    items = _depuis(client)
    assert [(i["kind"], i["aggregate_kind"]) for i in items] == [("aggregate", "temp"),
                                                                ("aggregate", "snd")]


def test_before_sert_aussi_le_type(store, client):
    t = NightTracker(store)
    t.on_wungt({"night": True, "tg2bd": "", "tendb": ""}, ts=1000.0)
    store.add_window_aggregate("hum", 50.0, 49.0, 51.0, None, ts=1050.0)
    t.on_wusts(1 << 11, ts=1100.0)
    t.on_wungt({"night": False, "tg2bd": "", "tendb": ""}, ts=1110.0)
    t.on_wusts(1, ts=1200.0)
    (nuit,) = client.get("/v1/sync", params={"before": 1e12}).json()["nights"]
    (a,) = nuit["aggregates"]
    assert (a["kind"], a["aggregate_kind"]) == ("hum", "hum")


def test_hist_reste_une_chaine_json(store, client):
    """Écrit au contrat (écart 11) : l'app range `hist` en TEXT ; un objet casserait son insertion."""
    store.add_window_aggregate("snd", 30, 26, 40, HIST, ts=100.0)
    (i,) = _depuis(client)
    assert isinstance(i["hist"], str) and json.loads(i["hist"]) == HIST


# ---- écart 11 : au changement ----------------------------------------------------------------
def test_un_agregat_inchange_ne_se_repete_pas(store):
    assert store.add_window_aggregate("temp", 20.0, 19.9, 20.1, None, ts=100.0) is not None
    assert store.add_window_aggregate("temp", 20.0, 19.9, 20.1, None, ts=400.0) is None
    assert [r["ts"] for r in _agregats(store)] == [100.0]          # l'heure de première lecture


def test_un_agregat_change_s_ajoute(store):
    store.add_window_aggregate("temp", 20.0, 19.9, 20.1, None, ts=100.0)
    store.add_window_aggregate("temp", 20.2, 19.9, 20.4, None, ts=400.0)
    assert len(_agregats(store)) == 2


def test_hist_compte_dans_la_comparaison(store):
    store.add_window_aggregate("snd", 30, 26, 40, HIST, ts=100.0)
    store.add_window_aggregate("snd", 30, 26, 40, {"ab": [1, [0, 40, 101]], "rl": [0]}, ts=400.0)
    assert len(_agregats(store)) == 2


def test_chaque_type_a_son_propre_dernier(store):
    store.add_window_aggregate("temp", 20.0, 20.0, 20.0, None, ts=100.0)
    store.add_window_aggregate("hum", 20.0, 20.0, 20.0, None, ts=100.1)    # mêmes nombres
    store.add_window_aggregate("temp", 20.0, 20.0, 20.0, None, ts=400.0)
    assert [r["kind"] for r in _agregats(store)] == ["temp", "hum"]


def test_on_compare_au_dernier_seulement(store):
    """A, B, A : la troisième n'est pas un doublon du dernier, elle s'ajoute."""
    for avg, ts in ((20.0, 100.0), (21.0, 400.0), (20.0, 700.0)):
        store.add_window_aggregate("temp", avg, avg, avg, None, ts=ts)
    assert [r["avg"] for r in _agregats(store)] == [20.0, 21.0, 20.0]


def test_pas_de_doublon_apres_redemarrage(tmp_path):
    chemin = tmp_path / "r.db"
    s = Store(chemin)
    s.add_window_aggregate("snd", 30, 26, 40, HIST, ts=100.0)
    s.close()
    s = Store(chemin)
    try:
        assert s.add_window_aggregate("snd", 30, 26, 40, HIST, ts=400.0) is None
        assert len(_agregats(s)) == 1
    finally:
        s.close()


async def test_la_collecte_n_ecrit_que_les_changements(fake, store):
    gw = DeviceGateway(fake.host, espacement_s=0.0)
    try:
        c = Collector(gw, store, Config())
        await c.tache_dataupload()
        await c.tache_dataupload()                                  # rien n'a changé
        assert len(_agregats(store)) == 4
        fake.corps[(1, "dataupload/temp.1/data")]["avtmp"] = 20.5    # une fenêtre de plus
        await c.tache_dataupload()
    finally:
        await gw.close()
    assert [r["kind"] for r in _agregats(store)] == ["temp", "hum", "snd", "lux", "temp"]
