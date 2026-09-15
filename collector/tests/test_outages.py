"""L'indisponibilité en cours survit aux redécouvertes (écart 10).

`.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`, écart 10 : l'id de l'indisponibilité
vivait dans l'instance de `Collector`, que le superviseur remplace à chaque redécouverte. La
nouvelle ne fermait rien, et `gateway()`, qui lisait la base, refusait tout pilotage alors que le
réveil répondait. Les tests de bout en bout passent par le vrai superviseur, contre le faux réveil.
"""
from __future__ import annotations

import asyncio

import pytest

from somneo_collector import __main__ as principal
from somneo_collector.collect import Collector
from somneo_collector.config import Config
from somneo_collector.gateway import DeviceGateway, Releve
from somneo_collector.outages import OutageTracker
from somneo_collector.state import RuntimeState
from somneo_collector.store import Store


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "somneo.db")
    yield s
    s.close()


async def _attendre(condition, delai_s: float = 10.0) -> None:
    fin = asyncio.get_running_loop().time() + delai_s
    while not condition():
        if asyncio.get_running_loop().time() > fin:
            raise AssertionError("condition jamais remplie")
        await asyncio.sleep(0.01)


def _superviseur(store, **cfg) -> principal.Superviseur:
    cfg.setdefault("synchro_ntp", None)      # pas d'attente de l'heure : pas de timesyncd ici
    return principal.Superviseur(store, Config(espacement_s=0.0, **cfg), RuntimeState())


# ---- de bout en bout : le mécanisme de l'écart 10 --------------------------------------------
async def test_une_redecouverte_ne_laisse_aucune_indisponibilite_ouverte(fake, store):
    """Le réveil tombe en panne assez longtemps pour que la collecte demande une redécouverte,
    puis revient : plus rien d'ouvert, et le pilotage passe. C'est le scénario vu sur la carte
    le 2026-09-14 (ids 11 et 15 ouverts, relevés normaux)."""
    sup = _superviseur(store, hote_force=fake.host)
    fake.panne = 500
    tache = asyncio.create_task(sup.run())
    try:
        await _attendre(lambda: sup.collector is not None)
        premier = sup.collector
        await _attendre(lambda: sup.collector is not premier)     # une redécouverte a eu lieu
        fake.panne = None
        await _attendre(lambda: store.latest_reading() is not None)
        assert store.open_outages() == []
        assert sup.gateway() is not None
    finally:
        sup.stop()
        await asyncio.wait_for(tache, timeout=5.0)


async def test_le_battement_continue_pendant_la_redecouverte(store, monkeypatch):
    """Le battement borne un arrêt du processus (plan §3). La redécouverte en fait partie : il
    doit battre même quand aucune collecte ne tourne."""
    monkeypatch.setattr(principal.discovery, "discover", lambda: None)
    monkeypatch.setattr(principal, "BACKOFF_MIN", 0.01)
    sup = _superviseur(store)
    tache = asyncio.create_task(sup.run())
    try:
        await _attendre(lambda: store.last_heartbeat() is not None, delai_s=3.0)
        assert sup.collector is None
    finally:
        sup.stop()
        await asyncio.wait_for(tache, timeout=5.0)


async def test_la_reparation_passe_avant_le_premier_battement(fake, store):
    """Une ligne laissée ouverte par le processus précédent se ferme à SON dernier battement.
    Si le battement de ce démarrage passait avant, elle serait datée de maintenant."""
    store.heartbeat(ts=1000.0)
    oid = store.open_outage("réveil injoignable", ts=900.0)
    sup = _superviseur(store, hote_force=fake.host)
    tache = asyncio.create_task(sup.run())
    try:
        await _attendre(lambda: store.last_heartbeat() > 1000.0)
    finally:
        sup.stop()
        await asyncio.wait_for(tache, timeout=5.0)
    ligne = next(o for o in store.recent_outages(0.0) if o["id"] == oid)
    assert ligne["end"] == 1000.0


# ---- le suivi --------------------------------------------------------------------------------
def _lignes(store) -> list[dict]:
    return sorted(store.recent_outages(0.0), key=lambda o: o["id"])


def test_meme_cause_une_seule_ligne(store):
    t = OutageTracker(store)
    assert t.report("réveil injoignable", ts=100.0) is True
    assert t.report("réveil injoignable", ts=110.0) is False
    assert t.report("réveil injoignable", ts=120.0) is False
    (ligne,) = _lignes(store)
    assert ligne["end"] is None and ligne["failures"] == 2    # même compte qu'avant : 0 à l'ouverture


def test_autre_cause_ferme_et_ouvre_sans_chevauchement(store):
    t = OutageTracker(store)
    t.report("réveil injoignable", ts=100.0)
    t.report("carte hors réseau", ts=150.0)
    t.report("réveil injoignable", ts=200.0)
    a, b, c = _lignes(store)
    assert (a["cause"], a["start"], a["end"]) == ("réveil injoignable", 100.0, 150.0)
    assert (b["cause"], b["start"], b["end"]) == ("carte hors réseau", 150.0, 200.0)
    assert (c["cause"], c["end"]) == ("réveil injoignable", None)
    assert [o["id"] for o in store.open_outages()] == [c["id"]] == [t.current]


def test_retablir_ferme_et_ne_touche_rien_sans_indisponibilite(store):
    t = OutageTracker(store)
    assert t.recover() is False
    seq = store.seq_courant()
    assert t.recover(ts=5.0) is False and store.seq_courant() == seq   # aucune écriture
    t.report("appareil saturé", ts=100.0)
    assert t.recover(ts=130.0) is True
    assert t.current is None and store.open_outages() == []
    assert _lignes(store)[0]["end"] == 130.0


# ---- réparation au démarrage -----------------------------------------------------------------
def test_reparation_au_premier_releve_qui_suit(store):
    """Le cas de la carte (ids 11 et 15) : chaque ligne se ferme au premier relevé qui suit son
    début, pas à celui qui suit la première ; un relevé antérieur ne compte pas."""
    store.add_reading({"mstmp": 20}, ts=50.0)
    a = store.open_outage("réveil injoignable", ts=100.0)
    store.add_reading({"mstmp": 20}, ts=160.0)
    b = store.open_outage("réveil injoignable", ts=200.0)
    store.add_reading({"mstmp": 20}, ts=290.0)
    store.add_reading({"mstmp": 20}, ts=350.0)
    store.heartbeat(ts=400.0)
    assert sorted(OutageTracker(store).repair_stale()) == [a, b]
    fins = {o["id"]: o["end"] for o in store.recent_outages(0.0)}
    assert fins == {a: 160.0, b: 290.0}


def test_reparation_au_dernier_battement_sans_releve(store):
    """Le processus s'est arrêté pendant la panne : le dernier battement borne la ligne."""
    store.add_reading({"mstmp": 20}, ts=50.0)
    store.open_outage("carte hors réseau", ts=100.0)
    store.heartbeat(ts=180.0)
    OutageTracker(store).repair_stale()
    assert store.recent_outages(0.0)[0]["end"] == 180.0


def test_reparation_jamais_avant_le_debut(store):
    """Un battement antérieur au début (il ne battait pas pendant la redécouverte, avant ce
    correctif) ne peut pas fermer une ligne avant qu'elle ne commence."""
    store.heartbeat(ts=90.0)
    store.open_outage("réveil injoignable", ts=100.0)
    OutageTracker(store).repair_stale()
    assert store.recent_outages(0.0)[0]["end"] == 100.0


def test_reparation_visible_par_le_rattrapage(store):
    """La fermeture reprend un `seq` : un téléphone à jour la reçoit par `since_seq`."""
    oid = store.open_outage("réveil injoignable", ts=100.0)
    store.add_reading({"mstmp": 20}, ts=130.0)
    avant = store.seq_courant()
    OutageTracker(store).repair_stale()
    rendus = [i for i in store.changes_since(avant) if i["kind"] == "outage"]
    assert [(i["id"], i["end"]) for i in rendus] == [(oid, 130.0)]


def test_reparation_sans_ligne_ouverte_n_ecrit_rien(store):
    t = OutageTracker(store)
    t.report("réveil injoignable", ts=100.0)
    t.recover(ts=110.0)
    seq = store.seq_courant()
    assert t.repair_stale() == [] and store.seq_courant() == seq


def test_reparation_epargne_l_indisponibilite_en_cours(store):
    t = OutageTracker(store)
    t.report("réveil injoignable", ts=100.0)
    assert t.repair_stale() == [] and t.current is not None
    assert store.open_outages()[0]["id"] == t.current


# ---- la collecte et la redécouverte partagent le suivi ---------------------------------------
async def test_deux_collectes_successives_partagent_l_indisponibilite(fake, store):
    """Le mécanisme de l'écart 10, sans superviseur : la collecte d'après la redécouverte ferme
    ce que celle d'avant a ouvert."""
    suivi = OutageTracker(store)
    gw = DeviceGateway(fake.host, espacement_s=0.0)
    try:
        avant = Collector(gw, store, Config(), outages=suivi)
        for _ in range(5):
            await avant._echec(Releve(1, "wusts", ok=False, status=500, error="HTTP 500"))
        apres = Collector(gw, store, Config(), outages=suivi)
        await apres._lire("wusts")
    finally:
        await gw.close()
    assert store.open_outages() == [] and suivi.current is None


async def test_redecouverte_change_la_cause_sans_chevauchement(store, monkeypatch):
    """La collecte a perdu le réveil (« réveil injoignable ») ; la redécouverte trouve la carte
    hors réseau, puis le réveil. Une seule ligne ouverte à tout instant, et la dernière attend un
    relevé : trouver l'adresse ne prouve pas que le réveil répond."""
    reponses = iter([OSError("Network is unreachable"), "192.0.2.1"])

    def discover():
        r = next(reponses)
        if isinstance(r, Exception):
            raise r
        return r

    monkeypatch.setattr(principal.discovery, "discover", discover)
    monkeypatch.setattr(principal, "BACKOFF_MIN", 0.01)
    sup = _superviseur(store)
    sup.outages.report("réveil injoignable")
    assert await sup._decouvrir() == "192.0.2.1"
    a, b = _lignes(store)
    assert a["cause"] == "réveil injoignable" and a["end"] == b["start"]
    assert b["cause"] == "carte hors réseau" and b["end"] is None
    assert sup.outages.current == b["id"]


def test_gateway_lit_le_suivi_pas_la_base(store):
    """Une ligne ouverte hors du suivi ne bloque pas le pilotage ; le suivi, si."""
    sup = _superviseur(store)
    sup.gw = passerelle = object()
    store.open_outage("réveil injoignable")
    assert sup.gateway() is passerelle
    sup.outages.report("appareil saturé")
    assert sup.gateway() is None
    sup.outages.recover()
    assert sup.gateway() is passerelle
