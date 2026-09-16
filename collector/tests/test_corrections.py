"""Corrections d'heure : la nuit garde le relevé, la correction se superpose (écarts 1, 2, 3).

`.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`, tranché le 2026-09-15 : une correction
n'écrase plus la valeur relevée. La nuit est servie partout — rattrapage compris — avec la valeur
qui fait foi, son origine, le relevé à côté et le journal des corrections. Une correction
`value: null` revient au relevé, et reste tracée.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.config import Config
from somneo_collector.nights import NightTracker
from somneo_collector.state import RuntimeState
from somneo_collector.store import Store

COUCHER, ALARME, CLOTURE, FIN_ALARME = 1000.0, 1100.0, 1110.0, 1200.0
OUVRE = {"night": True, "tg2bd": "", "tendb": ""}
CLOT = {"night": False, "tg2bd": "", "tendb": ""}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "c.db")
    yield s
    s.close()


@pytest.fixture
def client(store):
    return TestClient(create_app(store, Config(), RuntimeState()))


def _nuit_close(store) -> int:
    """Une nuit observée, close à la fin de l'alarme : coucher observé, lever estimé."""
    t = NightTracker(store)
    t.on_wungt(OUVRE, ts=COUCHER)
    t.on_wusts(1 << 11, ts=ALARME)
    t.on_wungt(CLOT, ts=CLOTURE)
    t.on_wusts(1, ts=FIN_ALARME)
    return store.list_nights(0, 1e12)[0]["id"]


def _brute(store, nid) -> dict:
    conn = store._ro()
    try:
        return dict(conn.execute("SELECT * FROM night WHERE id=?", (nid,)).fetchone())
    finally:
        conn.close()


def _corriger(client, nid, field, value):
    return client.post(f"/v1/nights/{nid}/corrections", json={"field": field, "value": value})


# ---- écart 1 : le relevé reste ---------------------------------------------------------------
def test_une_correction_ne_touche_pas_au_releve(store, client):
    nid = _nuit_close(store)
    assert _corriger(client, nid, "bedtime", 900.0).status_code == 200
    brute = _brute(store, nid)
    assert brute["bedtime"] == COUCHER and brute["bedtime_origin"] == "observed"


def test_la_machine_n_ecrase_pas_une_correction(store):
    """Le lever est corrigé pendant que la nuit attend la fin de l'alarme, puis le bit 11 retombe :
    `night_set_rise` écrit le relevé, la correction reste servie. Avant, elle était écrasée."""
    t = NightTracker(store)
    t.on_wungt(OUVRE, ts=COUCHER)
    t.on_wusts(1 << 11, ts=ALARME)
    t.on_wungt(CLOT, ts=CLOTURE)                                  # le lever attend l'alarme
    nid = store.get_awaiting_night()["id"]
    store.add_night_correction(nid, "risetime", 1500.0)
    t.on_wusts(1, ts=FIN_ALARME)                                  # la machine écrit le relevé
    n = store.get_night(nid)
    assert n["risetime"] == 1500.0 and n["risetime_origin"] == "corrected"
    assert (n["risetime_observed"], n["risetime_observed_origin"]) == (FIN_ALARME, "observed")


def test_recorrigee_la_derniere_fait_foi_le_releve_reste(store, client):
    """Cadrage backend §3.C, « nuit corrigée puis re-corrigée »."""
    nid = _nuit_close(store)
    _corriger(client, nid, "bedtime", 900.0)
    n = _corriger(client, nid, "bedtime", 950.0).json()["night"]
    assert n["bedtime"] == 950.0 and n["bedtime_observed"] == COUCHER
    assert [c["value"] for c in n["corrections"]] == [900.0, 950.0]


# ---- écart 2 : l'origine « corrigée » est servie ----------------------------------------------
def test_la_nuit_servie_porte_la_correction_et_le_releve(store, client):
    nid = _nuit_close(store)
    n = _corriger(client, nid, "bedtime", 900.0).json()["night"]
    assert (n["bedtime"], n["bedtime_origin"]) == (900.0, "corrected")
    assert (n["bedtime_observed"], n["bedtime_observed_origin"]) == (COUCHER, "observed")
    # le champ non corrigé : servi et relevé confondus
    assert (n["risetime"], n["risetime_origin"]) == (FIN_ALARME, "observed")
    assert (n["risetime_observed"], n["risetime_observed_origin"]) == (FIN_ALARME, "observed")
    assert [(c["field"], c["value"]) for c in n["corrections"]] == [("bedtime", 900.0)]


def test_une_nuit_jamais_corrigee_est_servie_pareil(store, client):
    nid = _nuit_close(store)
    n = client.get(f"/v1/nights/{nid}").json()
    assert (n["bedtime"], n["bedtime_origin"]) == (COUCHER, "observed")
    assert n["bedtime_observed"] == COUCHER and n["corrections"] == []


# ---- réversible : la correction « retour » ---------------------------------------------------
def test_retour_au_releve_reste_trace(store, client):
    nid = _nuit_close(store)
    _corriger(client, nid, "bedtime", 900.0)
    n = _corriger(client, nid, "bedtime", None).json()["night"]
    assert (n["bedtime"], n["bedtime_origin"]) == (COUCHER, "observed")
    assert [c["value"] for c in n["corrections"]] == [900.0, None]


def test_retour_puis_nouvelle_correction(store, client):
    nid = _nuit_close(store)
    _corriger(client, nid, "bedtime", 900.0)
    _corriger(client, nid, "bedtime", None)
    n = _corriger(client, nid, "bedtime", 950.0).json()["night"]
    assert (n["bedtime"], n["bedtime_origin"]) == (950.0, "corrected")


def test_retour_d_un_champ_sur_deux(store, client):
    nid = _nuit_close(store)
    _corriger(client, nid, "bedtime", 900.0)
    _corriger(client, nid, "risetime", 1300.0)
    n = _corriger(client, nid, "bedtime", None).json()["night"]
    assert n["bedtime_origin"] == "observed"
    assert (n["risetime"], n["risetime_origin"]) == (1300.0, "corrected")


def test_retour_sans_correction_n_ecrit_rien(store, client):
    """Rien à annuler : pas d'écriture, pas d'erreur — un renvoi après une coupure réseau ne doit
    pas échouer."""
    nid = _nuit_close(store)
    seq = store.seq_courant()
    r = _corriger(client, nid, "risetime", None)
    assert r.status_code == 200 and r.json()["night"]["corrections"] == []
    assert store.seq_courant() == seq


def test_retour_refuse_s_il_met_le_lever_avant_le_coucher(store, client):
    """Revenir au lever relevé le placerait avant le coucher servi : refusé, rien d'écrit."""
    nid = _nuit_close(store)
    _corriger(client, nid, "risetime", 1400.0)
    _corriger(client, nid, "bedtime", 1300.0)
    seq = store.seq_courant()
    assert _corriger(client, nid, "risetime", None).status_code == 422
    assert store.seq_courant() == seq


def test_la_valeur_est_obligatoire(store, client):
    """`null` veut dire « retour » ; l'absence de valeur n'est pas un retour."""
    nid = _nuit_close(store)
    r = client.post(f"/v1/nights/{nid}/corrections", json={"field": "bedtime"})
    assert r.status_code == 422


# ---- écart 3 : le rattrapage sert la nuit corrigée -------------------------------------------
def test_since_seq_renvoie_la_nuit_corrigee_servie(store, client):
    nid = _nuit_close(store)
    marque = store.seq_courant()
    _corriger(client, nid, "bedtime", 900.0)
    items = client.get("/v1/sync", params={"since_seq": marque}).json()["items"]
    (n,) = [i for i in items if i["kind"] == "night"]
    assert n["id"] == nid and n["seq"] > marque
    assert (n["bedtime"], n["bedtime_origin"], n["bedtime_observed"]) == (900.0, "corrected", COUCHER)
    assert len(n["corrections"]) == 1


def test_since_seq_renvoie_le_retour(store, client):
    nid = _nuit_close(store)
    _corriger(client, nid, "bedtime", 900.0)
    marque = store.seq_courant()
    _corriger(client, nid, "bedtime", None)
    items = client.get("/v1/sync", params={"since_seq": marque}).json()["items"]
    (n,) = [i for i in items if i["kind"] == "night"]
    assert (n["bedtime"], n["bedtime_origin"]) == (COUCHER, "observed")


def test_before_sert_les_nuits_corrigees(store, client):
    nid = _nuit_close(store)
    _corriger(client, nid, "risetime", 1300.0)
    (n,) = client.get("/v1/sync", params={"before": 1e12}).json()["nights"]
    assert (n["risetime"], n["risetime_origin"], n["risetime_observed"]) == (1300.0, "corrected", FIN_ALARME)
    assert len(n["corrections"]) == 1


def test_la_liste_des_nuits_est_servie(store, client):
    nid = _nuit_close(store)
    _corriger(client, nid, "bedtime", 900.0)
    (n,) = client.get("/v1/nights", params={"from": 0}).json()["nights"]
    assert n["bedtime_origin"] == "corrected" and len(n["corrections"]) == 1


def test_le_resume_suit_les_heures_servies(store, client):
    """La correction fait foi : la couverture se compte entre les heures servies, comme avant."""
    for ts in (950.0, 1050.0):
        store.add_reading({"mstmp": 20.0}, ts=ts)
    nid = _nuit_close(store)
    assert client.get(f"/v1/nights/{nid}").json()["resume"]["couverture"] == 1
    _corriger(client, nid, "bedtime", 900.0)
    assert client.get(f"/v1/nights/{nid}").json()["resume"]["couverture"] == 2


# ---- migration du schéma ---------------------------------------------------------------------
def test_base_neuve_au_schema_v2(store):
    conn = store._ro()
    try:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    finally:
        conn.close()


def test_migration_v1_vers_v2_garde_les_corrections(tmp_path):
    """La base de la carte est en v1, où `value` est NOT NULL : le retour ne pourrait pas s'y
    écrire. La migration rebâtit la table sans rien perdre, une seule fois."""
    chemin = tmp_path / "v1.db"
    s = Store(chemin)
    nid = s.create_night("2026-01-01", COUCHER, None)
    s.close()
    conn = sqlite3.connect(chemin)
    conn.executescript("""
        DROP TABLE night_correction;
        CREATE TABLE night_correction (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            seq       INTEGER NOT NULL,
            night_id  INTEGER NOT NULL REFERENCES night (id),
            ts        REAL NOT NULL,
            field     TEXT NOT NULL,
            value     REAL NOT NULL
        );
        PRAGMA user_version = 1;
    """)
    # son seq sort du compteur, comme dans une vraie base : un seq inventé plus grand que le
    # compteur ferait passer la correction suivante avant elle
    seq_v1 = conn.execute("SELECT value FROM meta WHERE key='next_seq'").fetchone()[0]
    conn.execute("INSERT INTO night_correction (seq, night_id, ts, field, value) "
                 "VALUES (?, ?, 5.0, 'bedtime', 900.0)", (seq_v1, nid))
    conn.execute("UPDATE meta SET value = value + 1 WHERE key='next_seq'")
    conn.commit()
    conn.close()

    for _ in range(2):                                   # la seconde ouverture ne migre plus
        s = Store(chemin)
        try:
            conn = s._ro()
            try:
                assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
                colonnes = {r[1]: r[3] for r in conn.execute("PRAGMA table_info(night_correction)")}
                assert colonnes["value"] == 0                                  # NULL permis
                assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            finally:
                conn.close()
            assert s.get_night(nid)["corrections"][0]["seq"] == seq_v1
        finally:
            s.close()

    s = Store(chemin)
    try:
        s.add_night_correction(nid, "bedtime", None)
        n = s.get_night(nid)
        assert (n["bedtime"], len(n["corrections"])) == (COUCHER, 2)
    finally:
        s.close()
