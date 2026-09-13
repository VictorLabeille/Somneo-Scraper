"""L'invariant d'architecture : seul `gateway.py` importe `pysomneo` (plan §2).

Ce test casse si un autre module se met à parler à l'appareil — c'est ce qui garantit la
connexion unique par construction, pas par discipline."""
from __future__ import annotations

import ast
import pathlib

import somneo_collector

RACINE = pathlib.Path(somneo_collector.__file__).parent


def _importe_pysomneo(source: str) -> bool:
    """Vrai si le module IMPORTE pysomneo (une mention en prose/docstring ne compte pas)."""
    for noeud in ast.walk(ast.parse(source)):
        if isinstance(noeud, ast.Import) and any(a.name.split(".")[0] == "pysomneo"
                                                 for a in noeud.names):
            return True
        if isinstance(noeud, ast.ImportFrom) and (noeud.module or "").split(".")[0] == "pysomneo":
            return True
    return False


def test_seul_gateway_importe_pysomneo():
    coupables = [str(py.relative_to(RACINE)) for py in RACINE.rglob("*.py")
                 if py.name != "gateway.py" and _importe_pysomneo(py.read_text(encoding="utf-8"))]
    assert not coupables, f"modules qui importent pysomneo hors gateway : {coupables}"
