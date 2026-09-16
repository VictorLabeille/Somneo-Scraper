"""Les nuits : référentiel NTP, lever à la fin de l'alarme, expiration anormale, recoucher, corrections."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.config import Config
from somneo_collector.nights import NightTracker
from somneo_collector.state import RuntimeState
from somneo_collector.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "n.db")
    yield s
    s.close()


def _wungt(night, tg2bd="2026-01-01T22:00:00+01:00", tendb=""):
    return {"night": night, "tg2bd": tg2bd, "tendb": tendb}


def test_heure_est_l_observation_ntp_pas_tg2bd(store):
    t = NightTracker(store)
    t.on_wungt(_wungt(True, tg2bd="2000-01-01T00:00:00+01:00"), ts=1000.0)
    n = store.get_open_night()
    assert n["bedtime"] == 1000.0                         # l'instant observé (NTP), pas tg2bd
    assert n["raw_tg2bd"] == "2000-01-01T00:00:00+01:00"  # brut gardé pour la traçabilité
    assert n["bedtime_origin"] == "observed"


def test_lever_a_la_fin_de_l_alarme_puis_recoucher(store):
    t = NightTracker(store)
    t.on_wungt(_wungt(True), ts=1000.0)                   # coucher
    t.on_wusts(1 << 11, ts=25000.0)                       # l'alarme sonne
    t.on_wungt(_wungt(False), ts=25010.0)                 # firmware clôt, alarme encore active
    assert store.get_open_night() is None
    assert store.get_awaiting_night() is not None          # lever en attente de la fin d'alarme
    t.on_wusts(1, ts=25400.0)                             # bit 11 retombe : fin de l'alarme
    assert store.get_awaiting_night() is None
    # recoucher : une seconde session
    t.on_wungt(_wungt(True), ts=90000.0)
    nuits = store.list_nights(0, 1e12)
    assert len(nuits) == 2
    premiere = [n for n in nuits if n["bedtime"] == 1000.0][0]
    assert premiere["state"] == "closed" and premiere["risetime"] == 25400.0
    assert premiere["risetime_origin"] == "observed"


def test_expiration_sans_alarme_est_anormale(store):
    t = NightTracker(store)
    t.on_wungt(_wungt(True), ts=1000.0)
    t.on_wungt(_wungt(False), ts=1000.0 + 12 * 3600)      # 12 h plus tard, aucune alarme
    n = store.list_nights(0, 1e12)[0]
    assert n["state"] == "abnormal" and n["risetime"] is None


def test_reprise_apres_redemarrage(store):
    NightTracker(store).on_wungt(_wungt(True), ts=1000.0)     # nuit ouverte, tracker jeté
    t2 = NightTracker(store)                                   # nouveau tracker : reprend l'état
    t2.on_wungt(_wungt(False), ts=1000.0 + 12 * 3600)         # sans alarme → anormale
    assert store.list_nights(0, 1e12)[0]["state"] == "abnormal"


def test_api_nuit_resume_et_correction(store):
    t = NightTracker(store)
    t.on_wungt(_wungt(True), ts=1000.0)
    for i in range(3):
        store.add_reading({"mstmp": 20.0 + i, "mslux": i}, ts=1000.0 + i * 10)
    t.on_wusts(1 << 11, ts=1000.0 + 100)
    t.on_wungt(_wungt(False), ts=1000.0 + 110)
    t.on_wusts(1, ts=1000.0 + 200)
    nid = store.list_nights(0, 1e12)[0]["id"]

    c = TestClient(create_app(store, Config(), RuntimeState()))
    nuit = c.get(f"/v1/nights/{nid}").json()
    assert nuit["resume"]["grandeurs"]["mstmp"]["max"] == 22.0
    # correction : lever avant coucher refusé
    assert c.post(f"/v1/nights/{nid}/corrections",
                  json={"field": "risetime", "value": 500.0}).status_code == 422
    # correction valide : la valeur servie suit, l'origine relevée reste consultable
    r = c.post(f"/v1/nights/{nid}/corrections", json={"field": "risetime", "value": 1000.0 + 300})
    assert r.status_code == 200
    assert r.json()["night"]["risetime"] == 1300.0
    assert len(r.json()["night"]["corrections"]) == 1
