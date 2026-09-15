"""« Collecteur arrêté » et l'heure au démarrage (écart 5).

`.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`, tranché le 2026-09-15 : au démarrage,
le suivi ouvre « collecteur arrêté » au dernier signe de vie du processus précédent — le plus
tardif de son dernier battement et de son dernier relevé — et le premier relevé réussi la ferme.
Aucun seuil. Un dernier battement à l'arrêt propre. Et avant tout, la collecte attend que l'heure
de la carte soit synchronisée, 5 min au plus : après une coupure, elle repart en retard.
"""
from __future__ import annotations

import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from somneo_collector import __main__ as principal
from somneo_collector.api import create_app
from somneo_collector.config import Config
from somneo_collector.outages import OutageTracker
from somneo_collector.state import RuntimeState
from somneo_collector.store import Store

ARRET = "collecteur arrêté"


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "s.db")
    yield s
    s.close()


async def _attendre(condition, delai_s: float = 10.0) -> None:
    fin = asyncio.get_running_loop().time() + delai_s
    while not condition():
        if asyncio.get_running_loop().time() > fin:
            raise AssertionError("condition jamais remplie")
        await asyncio.sleep(0.01)


def _lignes(store) -> list[dict]:
    return sorted(store.recent_outages(0.0), key=lambda o: o["id"])


def _superviseur(store, **cfg) -> principal.Superviseur:
    cfg.setdefault("synchro_ntp", None)
    return principal.Superviseur(store, Config(espacement_s=0.0, **cfg), RuntimeState())


# ---- le suivi --------------------------------------------------------------------------------
def test_arret_ouvert_au_dernier_signe_de_vie(store):
    store.heartbeat(ts=1000.0)
    store.add_reading({"mstmp": 20}, ts=1030.0)            # un relevé après le dernier battement
    t = OutageTracker(store)
    oid = t.open_stopped()
    (ligne,) = _lignes(store)
    assert (ligne["id"], ligne["cause"], ligne["start"], ligne["end"]) == (oid, ARRET, 1030.0, None)
    assert t.current == oid


def test_arret_ouvert_au_battement_s_il_est_le_plus_tardif(store):
    store.add_reading({"mstmp": 20}, ts=950.0)
    store.heartbeat(ts=1000.0)
    OutageTracker(store).open_stopped()
    assert _lignes(store)[0]["start"] == 1000.0


def test_base_neuve_pas_d_arret(store):
    """Ni battement ni relevé : il n'y avait pas de collecteur à arrêter."""
    t = OutageTracker(store)
    assert t.open_stopped() is None and _lignes(store) == [] and t.current is None


def test_le_premier_releve_ferme_l_arret(store):
    store.heartbeat(ts=1000.0)
    t = OutageTracker(store)
    t.open_stopped()
    t.recover(ts=1500.0)
    assert _lignes(store)[0]["end"] == 1500.0 and store.open_outages() == []


def test_un_echec_a_la_reprise_prend_le_relais_sans_trou(store):
    store.heartbeat(ts=1000.0)
    t = OutageTracker(store)
    t.open_stopped()
    t.report("réveil injoignable", ts=1500.0)
    a, b = _lignes(store)
    assert (a["cause"], a["end"]) == (ARRET, 1500.0)
    assert (b["cause"], b["start"], b["end"]) == ("réveil injoignable", 1500.0, None)


def test_une_fin_ne_precede_jamais_son_debut(store):
    """L'horloge revenue en arrière (délai d'attente dépassé, synchronisation tardive) : une
    indisponibilité se ferme au plus tôt à son début."""
    futur = time.time() + 3600
    store.heartbeat(ts=futur)
    t = OutageTracker(store)
    t.open_stopped()
    t.recover()
    assert _lignes(store)[0]["end"] == futur


def test_reparation_puis_arret_sans_chevauchement(store):
    store.heartbeat(ts=1000.0)
    ancienne = store.open_outage("réveil injoignable", ts=900.0)
    t = OutageTracker(store)
    t.repair_stale()
    t.open_stopped()
    a, b = _lignes(store)
    assert (a["id"], a["end"]) == (ancienne, 1000.0)
    assert (b["cause"], b["start"], b["end"]) == (ARRET, 1000.0, None)


# ---- de bout en bout -------------------------------------------------------------------------
async def test_un_arret_est_nomme_de_bout_en_bout(fake, store):
    hb = time.time() - 3600
    store.heartbeat(ts=hb)
    sup = _superviseur(store, hote_force=fake.host)
    tache = asyncio.create_task(sup.run())
    try:
        await _attendre(lambda: store.latest_reading() is not None)
        (ligne,) = [o for o in _lignes(store) if o["cause"] == ARRET]
        assert ligne["start"] == hb and ligne["end"] is not None and ligne["end"] > hb
        assert store.open_outages() == [] and sup.gateway() is not None
    finally:
        sup.stop()
        await asyncio.wait_for(tache, timeout=5.0)


async def test_dernier_battement_a_l_arret_propre(fake, store):
    sup = _superviseur(store, hote_force=fake.host)
    tache = asyncio.create_task(sup.run())
    await _attendre(lambda: store.last_heartbeat() is not None)
    avant = time.time()
    sup.stop()
    await asyncio.wait_for(tache, timeout=5.0)
    assert store.last_heartbeat() >= avant


# ---- l'heure d'abord -------------------------------------------------------------------------
async def test_rien_ne_se_date_avant_l_heure(fake, store, tmp_path):
    """Sans le drapeau de timesyncd, ni battement, ni réparation, ni collecte ; il arrive, et tout
    part."""
    drapeau = tmp_path / "synchronized"
    store.heartbeat(ts=1000.0)
    oid = store.open_outage("réveil injoignable", ts=900.0)
    sup = _superviseur(store, hote_force=fake.host, synchro_ntp=drapeau)
    tache = asyncio.create_task(sup.run())
    try:
        await asyncio.sleep(0.5)
        assert store.last_heartbeat() == 1000.0
        assert [o["id"] for o in store.open_outages()] == [oid]
        assert sup.collector is None and store.latest_reading() is None
        drapeau.touch()
        await _attendre(lambda: store.latest_reading() is not None)
    finally:
        sup.stop()
        await asyncio.wait_for(tache, timeout=5.0)


async def test_passe_le_delai_la_collecte_part_et_le_dit(fake, store, tmp_path):
    drapeau = tmp_path / "synchronized"                         # jamais posé
    sup = _superviseur(store, hote_force=fake.host, synchro_ntp=drapeau, attente_synchro_s=0.2)
    tache = asyncio.create_task(sup.run())
    try:
        await _attendre(lambda: store.latest_reading() is not None)
    finally:
        sup.stop()
        await asyncio.wait_for(tache, timeout=5.0)
    statut = TestClient(create_app(store, sup.cfg, sup.state)).get("/v1/status").json()
    assert statut["collecteur"]["heure_synchronisee"] is False


async def test_arret_pendant_l_attente_ne_date_rien(store, tmp_path):
    sup = _superviseur(store, synchro_ntp=tmp_path / "jamais")
    tache = asyncio.create_task(sup.run())
    await asyncio.sleep(0.2)
    sup.stop()
    await asyncio.wait_for(tache, timeout=3.0)
    assert store.last_heartbeat() is None                       # aucun battement d'heure douteuse


def test_le_statut_dit_si_l_heure_est_synchronisee(store, tmp_path):
    drapeau = tmp_path / "synchronized"
    c = TestClient(create_app(store, Config(synchro_ntp=drapeau), RuntimeState()))
    assert c.get("/v1/status").json()["collecteur"]["heure_synchronisee"] is False
    drapeau.touch()
    assert c.get("/v1/status").json()["collecteur"]["heure_synchronisee"] is True
    sans = TestClient(create_app(store, Config(synchro_ntp=None), RuntimeState()))
    assert sans.get("/v1/status").json()["collecteur"]["heure_synchronisee"] is None
