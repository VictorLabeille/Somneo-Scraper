"""Les seize profils d'alarme et l'instantané des réglages (écart 7).

`.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`, tranché le 2026-09-15 : la collecte
relit les seize profils une fois par jour, et après un changement de `aenvs`/`aalms`, seulement
entre 12 h et 18 h (heure de Paris) ; chaque profil est historisé à son changement ;
`GET /v1/settings/snapshot` les sert en lecture seule. La sélection et la relecture d'un profil se
font d'un bloc (`read_profile`), pour le relais comme pour la collecte.
"""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.collect import Collector
from somneo_collector.config import Config, FenetreProfils
from somneo_collector.gateway import DeviceGateway, Releve
from somneo_collector.nights import NightTracker
from somneo_collector.relay import Relay
from somneo_collector.store import Store

TOUJOURS = FenetreProfils(debut_h=0, fin_h=24)
JAMAIS = FenetreProfils(debut_h=0, fin_h=0)


@pytest.fixture
async def banc(fake, tmp_path):
    store = Store(tmp_path / "p.db")
    gw = DeviceGateway(fake.host, espacement_s=0.0)
    yield store, gw
    await gw.close()
    store.close()


def _selections(fake) -> list[int]:
    return [c["prfnr"] for (cle, c) in fake.puts if cle == (1, "wualm") and "prfnr" in c]


def _profils_en_base(store) -> list[int]:
    conn = store._ro()
    try:
        return [json.loads(r[0])["prfnr"] for r in conn.execute(
            "SELECT body FROM port_change WHERE port='wualm/prfwu' ORDER BY seq")]
    finally:
        conn.close()


# ---- la paire sélection + relecture ----------------------------------------------------------
async def test_des_lectures_simultanees_rendent_chacune_son_profil(banc):
    _, gw = banc
    relus = await asyncio.gather(*(gw.read_profile(n) for n in (3, 5, 7, 3)))
    assert [r.corps["prfnr"] for r in relus] == [3, 5, 7, 3]


async def test_le_relais_et_la_collecte_ne_se_melangent_pas(banc, fake):
    store, gw = banc
    relay = Relay(store, NightTracker(store), lambda: gw)
    c = Collector(gw, store, Config(profils=TOUJOURS), relay=relay)
    _, deux, neuf = await asyncio.gather(c.tache_profils(), relay.get_alarm(2), relay.get_alarm(9))
    assert deux.body["profile"]["prfnr"] == 2 and neuf.body["profile"]["prfnr"] == 9
    instantane = store.profiles_snapshot()
    assert sorted(instantane) == list(range(1, 17))                # aucun passage interrompu
    assert all(p["body"] == fake.profils[n] for n, p in instantane.items())


# ---- la tâche --------------------------------------------------------------------------------
async def test_seize_profils_relus_en_journee(banc, fake):
    store, gw = banc
    await Collector(gw, store, Config(profils=TOUJOURS)).tache_profils()
    assert _selections(fake) == list(range(1, 17))
    assert sorted(_profils_en_base(store)) == list(range(1, 17))


async def test_hors_fenetre_rien_n_est_selectionne(banc, fake):
    store, gw = banc
    await Collector(gw, store, Config(profils=JAMAIS)).tache_profils()
    assert fake.total == 0 and _profils_en_base(store) == []


async def test_une_fois_par_jour(banc, fake):
    store, gw = banc
    c = Collector(gw, store, Config(profils=TOUJOURS))
    await c.tache_profils()
    requetes = fake.total
    await c.tache_profils()
    assert fake.total == requetes


async def test_relus_apres_un_changement_seul_le_profil_change_s_ajoute(banc, fake):
    store, gw = banc
    c = Collector(gw, store, Config(profils=TOUJOURS))
    await c.tache_reglages()
    await c.tache_profils()
    fake.profils[4]["almhr"] = 6                                  # réglé à la façade
    fake.synchroniser()
    await c.tache_reglages()                                      # la collecte voit aalms changer
    await c.tache_profils()
    assert _selections(fake) == list(range(1, 17)) * 2
    en_base = _profils_en_base(store)
    assert len(en_base) == 17 and en_base.count(4) == 2


async def test_le_lendemain_ils_sont_relus_sans_lignes_de_plus(banc, fake):
    store, gw = banc
    c = Collector(gw, store, Config(profils=TOUJOURS))
    await c.tache_profils()
    c._profils_lus = ("2000-01-01",) + c._profils_lus[1:]
    await c.tache_profils()
    assert len(_selections(fake)) == 32 and len(_profils_en_base(store)) == 16


async def test_un_profil_relu_de_travers_interrompt_sans_rien_ecrire(banc, monkeypatch):
    store, gw = banc

    async def de_travers(n):
        return Releve(1, "wualm/prfwu", True, 200, body={"prfnr": n + 1}, observed_at=1.0)

    monkeypatch.setattr(gw, "read_profile", de_travers)
    c = Collector(gw, store, Config(profils=TOUJOURS))
    await c.tache_profils()
    assert _profils_en_base(store) == [] and store.open_outages() == []
    assert c._profils_lus is None                                 # repris au passage suivant


async def test_une_panne_ouvre_une_indisponibilite_puis_reprend(banc, fake):
    store, gw = banc
    c = Collector(gw, store, Config(profils=TOUJOURS))
    fake.panne = 500
    await c.tache_profils()
    assert store.open_outages()[0]["cause"] == "appareil saturé" and _profils_en_base(store) == []
    fake.panne = None
    await c.tache_profils()
    assert store.open_outages() == [] and len(_profils_en_base(store)) == 16


# ---- l'instantané ----------------------------------------------------------------------------
async def test_instantane_des_reglages(banc, fake):
    store, gw = banc
    client = TestClient(create_app(store, Config()))
    vide = client.get("/v1/settings/snapshot").json()
    assert vide["profiles"] == {} and vide["complete"] is False
    c = Collector(gw, store, Config(profils=TOUJOURS))
    await c.tache_reglages()
    await c.tache_profils()
    snap = client.get("/v1/settings/snapshot").json()
    assert snap["complete"] is True
    assert sorted(snap["profiles"], key=int) == [str(n) for n in range(1, 17)]
    assert snap["profiles"]["1"]["body"]["almhr"] == 7 and snap["profiles"]["1"]["since"]
    assert set(snap["ports"]) == {"wulgt", "wudsk", "wualm", "wualm/aenvs", "wualm/aalms", "wuply"}
    assert snap["ports"]["wualm/aenvs"]["body"]["prfen"][0] is True


async def test_le_relais_refuse_un_profil_relu_de_travers(banc, monkeypatch):
    """Le relais vérifie aussi le `prfnr` relu : un autre profil n'est ni servi ni historisé."""
    store, gw = banc

    async def de_travers(n):
        return Releve(1, "wualm/prfwu", True, 200, body={"prfnr": n + 1}, observed_at=1.0)

    monkeypatch.setattr(gw, "read_profile", de_travers)
    res = await Relay(store, NightTracker(store), lambda: gw).get_alarm(3)
    assert res.status == 503 and store.profiles_snapshot() == {}


async def test_le_relais_alimente_l_instantane(banc):
    store, gw = banc
    relay = Relay(store, NightTracker(store), lambda: gw)
    await relay.get_alarm(3)
    assert list(store.profiles_snapshot()) == [3]
