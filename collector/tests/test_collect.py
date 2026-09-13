"""La collecte : chaque tâche écrit ce qu'il faut, et une panne ouvre une indisponibilité datée."""
from __future__ import annotations

import asyncio

import pytest

from somneo_collector.collect import Collector
from somneo_collector.config import Config
from somneo_collector.gateway import DeviceGateway, Releve
from somneo_collector.store import Store


@pytest.fixture
def collector(fake, tmp_path):
    gw = DeviceGateway(fake.host, espacement_s=0.0)
    store = Store(tmp_path / "somneo.db")
    yield Collector(gw, store, Config())
    gw.close()
    store.close()


async def test_wusrd_ecrit_un_reading(collector):
    await collector.tache_wusrd()
    r = collector.store.latest_reading()
    assert r["mstmp"] == 20 and r["avsnd"] == 30


async def test_dataupload_quatre_agregats(collector):
    await collector.tache_dataupload()
    conn = collector.store._ro()
    kinds = {row["kind"] for row in conn.execute("SELECT kind FROM window_aggregate")}
    conn.close()
    assert kinds == {"temp", "hum", "snd", "lux"}


async def test_reglages_et_liaison_en_ports(collector):
    await collector.tache_reglages()
    await collector.tache_liaison()
    assert collector.store.last_port_body("wulgt")["ltlvl"] == 15
    assert collector.store.last_port_body("backend")["dcs-state"] == "subscribed"
    # le port device alimente la table device
    conn = collector.store._ro()
    dev = conn.execute("SELECT * FROM device").fetchone()
    conn.close()
    assert dev["serial"] == "TESTSERIAL" and dev["firmware"] == "1.2.3"


async def test_horloge_ecrit_un_controle_sans_correction(collector):
    await collector.tache_horloge()
    c = collector.store.latest_clock_check()
    assert c["device_time"] is not None and c["wutim_time"] is not None
    assert c["corrected"] == 0


async def test_panne_ouvre_puis_ferme_une_indisponibilite(collector):
    # La logique d'indisponibilité se teste sur des relevés synthétiques : passer par un vrai
    # 500 déclencherait les réessais urllib3 de pysomneo (~6 s chacun) sans rien prouver de plus.
    saturé = Releve(1, "wusts", ok=False, status=500, error="HTTP 500")
    for _ in range(5):
        await collector._echec(saturé)
    ouvertes = collector.store.open_outages()
    assert len(ouvertes) == 1 and ouvertes[0]["cause"] == "appareil saturé"
    assert ouvertes[0]["failures"] == 4          # 1 à l'ouverture, puis 4 bumps
    # une lecture réussie referme
    await collector._lire("wusts")
    assert collector.store.open_outages() == []


async def test_cause_reveil_injoignable(collector):
    injoignable = Releve(1, "wusts", ok=False, status=None, error="ReadTimeout")
    await collector._echec(injoignable)
    assert collector.store.open_outages()[0]["cause"] == "réveil injoignable"


async def test_redecouverte_apres_seuil(collector):
    appels = []

    async def on_lost():
        appels.append(1)

    collector._on_lost = on_lost
    r = Releve(1, "wusts", ok=False, status=500, error="HTTP 500")
    for _ in range(6):
        await collector._echec(r)
    assert appels == [1]                          # une seule demande de redécouverte, au seuil (5e)


async def test_boucle_demarre_et_s_arrete(collector):
    task = asyncio.create_task(collector.run())
    await asyncio.sleep(0.3)                       # laisse un tour de boucle s'exécuter
    collector.stop()
    await asyncio.wait_for(task, timeout=3.0)
    assert collector.store.latest_reading() is not None      # au moins un tour a écrit
