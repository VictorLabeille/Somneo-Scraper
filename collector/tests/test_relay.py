"""Le relais du pilotage (incrément 4) : écrire → relire → confirmer, bornes, gestes de nuit.

Contre le faux réveil, qui applique les `PUT` comme l'appareil (`tests/fake_device.py`). Le
critère de l'incrément — aucune écriture n'a d'effet que l'utilisateur n'a pas demandé — se
valide sur l'appareil ; ici, ce qui peut l'être hors appareil : la valeur relue fait foi, rien ne
part hors bornes, un réglage de façade n'est pas écrasé, un geste n'est jamais perdu.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from somneo_collector.api import create_app
from somneo_collector.collect import Collector
from somneo_collector.config import Config
from somneo_collector.gateway import DeviceGateway, Releve, _vers_255
from somneo_collector.nights import NightTracker
from somneo_collector.relay import Relay
from somneo_collector.store import Store

WULGT, WUNGT = (1, "wulgt"), (1, "wungt")


@pytest.fixture
async def banc(tmp_path, fake):
    store = Store(tmp_path / "r.db")
    gw = DeviceGateway(fake.host, espacement_s=0.0)
    nights = NightTracker(store)
    lien = {"gw": gw}                       # lien["gw"] = None : réveil injoignable
    relay = Relay(store, nights, lambda: lien["gw"])
    yield SimpleNamespace(store=store, gw=gw, nights=nights, relay=relay, fake=fake, lien=lien)
    await gw.close()
    store.close()


def _puts(fake, port: str) -> list[dict]:
    return [charge for (cle, charge) in fake.puts if cle == (1, port)]


class GwMuette:
    """Une passerelle dont aucune requête ne passe : le réveil est parti entre deux lectures."""

    async def put(self, port, payload, produit=1):
        return Releve(produit, port, False, error="ConnectionError", injoignable=True)

    async def read(self, port, produit=1):
        return Releve(produit, port, False, error="ConnectionError", injoignable=True)


# ---- l'échelle de la lampe --------------------------------------------------------------
def test_vers_255_rend_exactement_chaque_niveau():
    """`pysomneo` tronque `brightness / 255 * 25` : chaque niveau 1-25 doit revenir intact."""
    for level in range(1, 26):
        assert int(_vers_255(level) / 255 * 25) == level, level


# ---- écrire → relire → confirmer ---------------------------------------------------------
async def test_lampe_relue_et_miroir_a_jour(banc):
    res = await banc.relay.light(True, 12)
    assert res.status == 200 and res.body["ok"] is True
    assert res.body["state"]["onoff"] is True and res.body["state"]["ltlvl"] == 12
    assert banc.fake.corps[WULGT]["ltlvl"] == 12
    vu = banc.store.last_port("wulgt")
    assert vu["body"]["ltlvl"] == 12 and vu["observed_at"] >= vu["since"]


async def test_temoin_un_cache_perime_ecrase_la_facade(banc):
    """Témoin positif : le danger que le cache vidé écarte existe bien. Sans vidage, la méthode
    de pysomneo renvoie le `wulgt` de son cache et défait un réglage fait à la façade."""
    await banc.gw.toggle_light(True, 12)               # remplit le cache de pysomneo
    banc.fake.corps[WULGT]["ctype"] = 3                 # quelqu'un change le thème à la façade
    await banc.gw.run(lambda: banc.gw._somneo.toggle_light(True, _vers_255(5)))  # sans vidage
    assert banc.fake.corps[WULGT]["ctype"] == 0         # écrasé : le témoin voit le défaut


async def test_cache_vide_la_facade_n_est_pas_ecrasee(banc):
    await banc.relay.light(True, 12)
    banc.fake.corps[WULGT]["ctype"] = 3
    res = await banc.relay.light(True, 5)
    assert res.status == 200
    assert banc.fake.corps[WULGT]["ctype"] == 3 and banc.fake.corps[WULGT]["ltlvl"] == 5


async def test_acceptee_mais_non_refletee_est_un_echec(banc):
    banc.fake.ignorer.add(WULGT)                        # 200, mais rien d'appliqué
    res = await banc.relay.light(True, 20)
    assert res.status == 502 and res.body["ok"] is False
    assert "non reflétée" in res.body["reason"]
    assert res.body["mismatch"] == {"onoff": False, "ltlvl": 15}
    assert banc.store.last_port("wulgt")["body"]["ltlvl"] == 15   # le miroir suit l'appareil


async def test_refusee_par_le_reveil(banc):
    banc.fake.refuser[WULGT] = 500
    res = await banc.relay.light(True, 20)
    assert res.status == 502 and "refusée" in res.body["reason"]
    assert res.body["state"]["ltlvl"] == 15 and banc.fake.corps[WULGT]["ltlvl"] == 15


@pytest.mark.parametrize("appel", [
    lambda r: r.light(True, 0), lambda r: r.light(True, 26), lambda r: r.light(False, 10),
    lambda r: r.snooze(0), lambda r: r.snooze(21),
])
async def test_hors_bornes_rien_ne_part(banc, appel):
    res = await appel(banc.relay)
    assert res.status == 422
    assert banc.fake.total == 0                          # ni écriture, ni même une lecture


async def test_injoignable_sert_le_miroir_date(banc):
    banc.store.record_port_change("wulgt", {"onoff": False, "ltlvl": 7}, ts=1000.0)
    banc.lien["gw"] = None
    res = await banc.relay.light(True)
    assert res.status == 503 and res.body["state"]["ltlvl"] == 7
    assert res.body["observed_at"] == 1000.0
    assert banc.fake.total == 0


async def test_veilleuse(banc):
    assert (await banc.relay.nightlight(True)).status == 200
    assert banc.fake.corps[WULGT]["ngtlt"] is True and banc.fake.corps[WULGT]["onoff"] is False
    assert (await banc.relay.nightlight(False)).status == 200
    assert banc.fake.corps[WULGT]["ngtlt"] is False


async def test_coucher_de_soleil_marche_arret(banc):
    assert (await banc.relay.sunset(True)).status == 200
    assert banc.fake.corps[(1, "wudsk")]["onoff"] is True
    assert (await banc.relay.sunset(False)).status == 200
    assert banc.fake.corps[(1, "wudsk")]["onoff"] is False


async def test_rappel_global(banc):
    res = await banc.relay.snooze(12)
    assert res.status == 200 and res.body["state"]["snztm"] == 12
    assert banc.fake.corps[(1, "wualm")]["prfnr"] == 1           # la sélection n'a pas bougé


async def test_cardinal_ecritures_du_relais_et_lectures_melees(banc):
    """Le relais écrit par des méthodes pysomneo qui enchaînent plusieurs requêtes : mêlées à la
    collecte, l'appareil n'en voit toujours qu'une à la fois."""
    taches = [banc.relay.light(True, 9), banc.relay.nightlight(True), banc.relay.sunset(True),
              banc.relay.snooze(10), banc.relay.bedtime()]
    taches += [banc.gw.read("wusrd") for _ in range(10)]
    await asyncio.gather(*taches)
    assert banc.fake.max_concurrent == 1


# ---- gestes de nuit ---------------------------------------------------------------------
async def test_coucher_pris_par_le_reveil(banc):
    t0 = time.time()
    res = await banc.relay.bedtime()
    assert res.status == 201 and res.body["device"] == "pris"
    n = res.body["night"]
    assert n["state"] == "open" and n["bedtime_origin"] == "confirmed"
    assert t0 <= n["bedtime"] <= res.body["served_at"]            # l'heure de l'APPUI
    assert n["raw_tg2bd"]                                         # daté par le réveil, gardé brut
    assert banc.fake.corps[WUNGT]["night"] is True
    assert _puts(banc.fake, "wungt") == [{"night": True}]         # jamais de tg2bd envoyé


async def test_double_appui_ne_fait_rien(banc):
    premiere = (await banc.relay.bedtime()).body["night"]
    res = await banc.relay.bedtime()
    assert res.status == 200 and res.body["device"] == "déjà en cours"
    assert res.body["night"]["id"] == premiere["id"]
    assert res.body["night"]["bedtime"] == premiere["bedtime"]    # l'heure ne bouge pas
    assert len(_puts(banc.fake, "wungt")) == 1
    assert len(banc.store.list_nights(0, 1e12)) == 1


async def test_deux_appuis_simultanes_une_seule_nuit(banc):
    a, b = await asyncio.gather(banc.relay.bedtime(), banc.relay.bedtime())
    assert sorted((a.status, b.status)) == [200, 201]
    assert a.body["night"]["id"] == b.body["night"]["id"]
    assert len(_puts(banc.fake, "wungt")) == 1


async def test_coucher_injoignable_retenu_puis_rejoue(banc):
    banc.lien["gw"] = None
    res = await banc.relay.bedtime()
    assert res.status == 202 and res.body["device"] == "en attente du réveil"
    n = res.body["night"]
    assert n["state"] == "pending_device" and n["bedtime_origin"] == "confirmed"
    assert (await banc.relay.bedtime()).status == 202            # second appui : la même nuit
    assert banc.fake.puts == []
    await banc.relay.replay_pending(banc.gw)                      # le réveil répond de nouveau
    apres = banc.store.get_night(n["id"])
    assert apres["state"] == "open" and apres["bedtime"] == n["bedtime"]
    assert banc.fake.corps[WUNGT]["night"] is True
    assert banc.store.pending_gestures() == []
    assert len(banc.store.list_nights(0, 1e12)) == 1


async def test_rejeu_toujours_injoignable_garde_le_geste(banc):
    banc.lien["gw"] = None
    await banc.relay.bedtime()
    await banc.relay.replay_pending(GwMuette())
    assert len(banc.store.pending_gestures()) == 1
    assert banc.nights.current()["state"] == "pending_device"


async def test_coucher_retenu_adopte_par_l_observation(banc):
    """Le réveil revient et une session s'y ouvre avant le rejeu (SleepMapper, ou une écriture
    vue par la collecte) : la nuit retenue l'adopte, aucune seconde nuit."""
    banc.lien["gw"] = None
    n = (await banc.relay.bedtime()).body["night"]
    banc.nights.on_wungt({"night": True, "tg2bd": "X", "tendb": ""}, ts=n["bedtime"] + 600)
    apres = banc.store.get_night(n["id"])
    assert apres["state"] == "open" and apres["bedtime"] == n["bedtime"]
    assert apres["raw_tg2bd"] == "X"
    assert len(banc.store.list_nights(0, 1e12)) == 1
    await banc.relay.replay_pending(banc.gw)
    assert banc.fake.puts == []                                   # plus rien à rejouer


def test_observation_avant_notre_relecture(tmp_path):
    """Course : la collecte voit `night: true` entre notre PUT et notre relecture. La nuit
    qu'elle ouvre est la nôtre — elle prend l'heure de l'appui, pas celle de l'observation."""
    store = Store(tmp_path / "c.db")
    t = NightTracker(store)
    t.on_wungt({"night": True, "tg2bd": "Y", "tendb": ""}, ts=1030.0)
    nid = t.bedtime_taken(1000.0, "Y")
    n = store.get_night(nid)
    assert n["bedtime"] == 1000.0 and n["bedtime_origin"] == "confirmed"
    assert len(store.list_nights(0, 1e12)) == 1
    store.close()


async def test_lever_pris(banc):
    await banc.relay.bedtime()
    res = await banc.relay.risetime()
    assert res.status == 201
    n = res.body["night"]
    assert n["state"] == "closed" and n["risetime_origin"] == "confirmed"
    assert n["risetime"] >= n["bedtime"]
    assert banc.fake.corps[WUNGT]["night"] is False
    banc.nights.on_wungt(banc.fake.corps[WUNGT], ts=time.time())  # la collecte le voit après
    assert banc.store.get_night(n["id"])["state"] == "closed"     # pas « anormale »


async def test_lever_sans_nuit(banc):
    assert (await banc.relay.risetime()).status == 409


async def test_lever_apres_cloture_par_l_alarme(banc):
    """Le firmware a clos la session à l'heure de l'alarme, qui sonne encore : l'appui « je me
    lève » donne l'heure du lever, confirmée, sans rien écrire dans le réveil."""
    await banc.relay.bedtime()
    banc.nights.on_wusts(1 << 11, ts=time.time())
    banc.nights.on_wungt({"night": False, "tg2bd": "", "tendb": "A"}, ts=time.time())
    avant = len(banc.fake.puts)
    res = await banc.relay.risetime()
    assert res.status == 200 and res.body["night"]["risetime_origin"] == "confirmed"
    assert len(banc.fake.puts) == avant
    banc.nights.on_wusts(1, ts=time.time() + 60)                  # la fin d'alarme n'écrase rien
    assert banc.store.get_night(res.body["night"]["id"])["risetime_origin"] == "confirmed"


async def test_lever_injoignable_retenu_puis_rejoue(banc):
    await banc.relay.bedtime()
    banc.lien["gw"] = None
    res = await banc.relay.risetime()
    assert res.status == 202 and res.body["night"]["state"] == "closed"  # close ici, dès l'appui
    assert banc.fake.corps[WUNGT]["night"] is True
    await banc.relay.replay_pending(banc.gw)
    assert banc.fake.corps[WUNGT]["night"] is False
    assert banc.store.pending_gestures() == []


async def test_lever_sur_coucher_retenu(banc):
    """Le réveil n'a jamais reçu le coucher : le lever clôt la nuit ici, rien n'est rejoué."""
    banc.lien["gw"] = None
    await banc.relay.bedtime()
    res = await banc.relay.risetime()
    assert res.status == 200 and res.body["night"]["state"] == "closed"
    await banc.relay.replay_pending(banc.gw)
    assert banc.fake.puts == [] and banc.store.pending_gestures() == []


async def test_rejeu_refuse_abandonne_sans_boucle(banc):
    """Un geste que le réveil refuse n'est pas réessayé en boucle (AGENTS.md, 489 PUT) ; sa nuit
    sort de l'attente, sinon elle bloquerait tout coucher suivant."""
    banc.lien["gw"] = None
    n = (await banc.relay.bedtime()).body["night"]
    banc.fake.ignorer.add(WUNGT)
    await banc.relay.replay_pending(banc.gw)
    await banc.relay.replay_pending(banc.gw)
    assert len(_puts(banc.fake, "wungt")) == 1
    assert banc.store.pending_gestures() == []
    assert banc.store.get_night(n["id"])["state"] == "abnormal"
    assert banc.nights.current() is None


async def test_la_collecte_rejoue_les_gestes(banc):
    banc.lien["gw"] = None
    await banc.relay.bedtime()
    collector = Collector(banc.gw, banc.store, Config(), nights=banc.nights, relay=banc.relay)
    await collector.tache_gestes()
    assert banc.fake.corps[WUNGT]["night"] is True
    assert banc.nights.current()["state"] == "open"


# ---- l'API : miroir et codes ------------------------------------------------------------
def _aenvs(prfen: list, prfvs: list) -> dict:
    return {"prfen": prfen, "prfvs": prfvs, "pwrsv": [0, 0, 0] * len(prfen)}


def test_device_miroir_et_alarme_masquee_armee(tmp_path):
    store = Store(tmp_path / "d.db")
    store.record_port_change("wualm/aenvs", _aenvs([True, False, True] + [False] * 13,
                                                   [True, True, False] + [False] * 13))
    store.record_port_change("wualm/aalms", {"almhr": [7, 8] + [0] * 14,
                                             "almmn": [0, 30] + [0] * 14,
                                             "daynm": [254, 62] + [0] * 14})
    store.record_port_change("wusts", {"wusts": 2817})
    c = TestClient(create_app(store, Config()))
    d = c.get("/v1/device").json()
    assert [a["n"] for a in d["alarms"]] == [1, 2]
    assert d["alarms"][1] == {"n": 2, "enabled": False, "hour": 8, "minute": 30, "days": 62,
                              "powerwake": {"on": False, "hour": 0, "minute": 0}}
    assert d["hidden_armed_alarms"] == [3]
    assert d["wusts_bits"] == [0, 8, 9, 11]
    assert d["ports"]["wusts"]["observed_at"] >= d["ports"]["wusts"]["since"]
    assert c.get("/v1/status").json()["alarmes"]["masquees_armees"] == [3]
    store.close()


def test_api_codes_sans_reveil(tmp_path):
    store = Store(tmp_path / "a.db")
    c = TestClient(create_app(store, Config()))       # relais sans passerelle
    assert c.put("/v1/light", json={"on": True}).status_code == 503
    assert c.put("/v1/light", json={"on": "yes"}).status_code == 422     # booléen strict
    assert c.put("/v1/light", json={"on": True, "level": 30}).status_code == 422
    assert c.put("/v1/snooze", json={"minutes": 25}).status_code == 422
    r = c.post("/v1/nights/bedtime")
    assert r.status_code == 202 and r.json()["night"]["state"] == "pending_device"
    assert c.post("/v1/nights/risetime").status_code == 200
    assert c.post("/v1/nights/risetime").status_code == 409
    store.close()


def test_api_bout_en_bout_contre_le_faux_reveil(tmp_path, fake):
    """Par HTTP : routes asynchrones et passerelle réelle, dans la boucle du serveur de test."""
    store = Store(tmp_path / "h.db")
    gw = DeviceGateway(fake.host, espacement_s=0.0)
    relay = Relay(store, NightTracker(store), lambda: gw)
    with TestClient(create_app(store, Config(), relay=relay)) as c:
        r = c.put("/v1/light", json={"on": True, "level": 18})
        assert r.status_code == 200 and r.json()["state"]["ltlvl"] == 18
        assert c.post("/v1/nights/bedtime").status_code == 201
        assert c.get("/v1/device").json()["ports"]["wulgt"]["body"]["ltlvl"] == 18
        c.portal.call(gw.close)      # la session aiohttp vit dans la boucle du serveur de test
    store.close()
