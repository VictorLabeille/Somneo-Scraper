"""La passerelle : codes de réponse, produit 0, et l'invariant cardinal (une requête en vol)."""
from __future__ import annotations

import asyncio

import pytest

from somneo_collector.gateway import DeviceGateway


@pytest.fixture
def gw(fake):
    g = DeviceGateway(fake.host, espacement_s=0.0)   # pas d'espacement : on teste la concurrence
    yield g
    g.close()


async def test_lecture_200(gw):
    r = await gw.read("wusrd")
    assert r.ok and r.status == 200
    assert r.corps == {"mslux": 0, "mstmp": 20, "msrhu": 50, "mssnd": 30, "avlght": 0}
    assert r.observed_at > 0


async def test_port_inconnu_422(gw):
    r = await gw.read("zzzzz")
    assert not r.ok and r.status == 422
    assert r.corps is None and r.error


async def test_erreur_400(gw):
    r = await gw.read("err400")
    assert not r.ok and r.status == 400


async def test_produit_0_chemin_absolu(gw):
    r = await gw.read("time", produit=0)
    assert r.ok and r.corps["datetime"].startswith("2026-")


async def test_read_body_raccourci(gw):
    assert (await gw.read_body("wusts"))["wusts"] == 1
    assert await gw.read_body("zzzzz") is None


async def test_cardinal_une_seule_requete_en_vol(gw, fake):
    """Sous une rafale de 25 lectures concurrentes, l'appareil n'en voit jamais deux à la fois."""
    await asyncio.gather(*(gw.read("wusrd") for _ in range(25)))
    assert fake.total == 25
    assert fake.max_concurrent == 1, f"pic de concurrence {fake.max_concurrent}, attendu 1"


async def test_cardinal_lectures_et_ecritures_melees(gw, fake):
    """Même mélange lectures + appels `run` (écritures futures) : toujours une seule en vol."""
    def ecriture():
        return gw._somneo._client._internal_call("GET", "wulgt")   # simule un appel bloquant

    taches = [gw.read("wusts") for _ in range(10)] + [gw.run(ecriture) for _ in range(10)]
    await asyncio.gather(*taches)
    assert fake.max_concurrent == 1
