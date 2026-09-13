"""La passerelle : codes de réponse, produit 0, et l'invariant cardinal (une requête en vol)."""
from __future__ import annotations

import asyncio

import pytest

from somneo_collector.gateway import DeviceGateway


@pytest.fixture
async def gw(fake):
    g = DeviceGateway(fake.host, espacement_s=0.0)   # pas d'espacement : on teste la concurrence
    yield g
    await g.close()


async def test_lecture_200(gw):
    r = await gw.read("wusrd")
    assert r.ok and r.status == 200
    assert r.corps["mslux"] == 0 and r.corps["avsnd"] == 30
    assert r.observed_at > 0


async def test_port_inconnu_422(gw, fake):
    """Un `422` est un `422`, pas une panne de connexion. Témoin : la 6.0 (`13ec0c5`) l'envoie
    TROIS fois — `SomneoInvalidURLError` hérite de `ClientError`, que sa boucle de réessais
    attrape — puis le rend en `SomneoConnectionError`, que la passerelle déplie. Si ce nombre
    tombe à 1, la bibliothèque est corrigée : retirer le dépliage de `__cause__` (gateway.py)."""
    avant = fake.total
    r = await gw.read("zzzzz")
    assert not r.ok and r.status == 422 and not r.injoignable
    assert r.corps is None and r.error
    assert fake.total - avant == 3


async def test_put_422_n_est_pas_une_panne(gw, fake):
    r = await gw.put("zzzzz", {"x": 1})
    assert r.status == 422 and not r.injoignable
    assert len([c for (cle, c) in fake.puts if cle == (1, "zzzzz")]) == 3   # témoin, voir ci-dessus


async def test_erreur_400(gw):
    r = await gw.read("err400")
    assert not r.ok and r.status == 400


async def test_un_500_une_seule_requete(gw, fake):
    """Un `500` remonte tel quel, en une requête, et n'est pas une panne de connexion. La 5.0.6
    synchrone le réessayait douze fois en 39 s et le rendait en `RetryError` (plan §2) : c'est la
    prémisse du passage à la 6.0, et le critère « 24 h sans un `500` » en dépend."""
    avant = fake.total
    r = await gw.read("err500")
    assert not r.ok and r.status == 500 and not r.injoignable
    assert fake.total - avant == 1


async def test_un_put_refuse_n_est_envoye_qu_une_fois(gw, fake):
    fake.refuser[(1, "wulgt")] = 500
    r = await gw.put("wulgt", {"onoff": True})
    assert r.status == 500 and not r.injoignable
    assert [c for (cle, c) in fake.puts if cle == (1, "wulgt")] == [{"onoff": True}]


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
    """Même mélange lectures + appels `run` (écritures) : toujours une seule en vol."""
    def ecriture():
        return gw._somneo._client._internal_call("GET", "wulgt")   # une coroutine de pysomneo

    taches = [gw.read("wusts") for _ in range(10)] + [gw.run(ecriture) for _ in range(10)]
    await asyncio.gather(*taches)
    assert fake.max_concurrent == 1
