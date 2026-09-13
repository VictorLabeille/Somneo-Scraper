"""Routes de l'API, incrément 1 : `GET /v1/status` et `GET /v1/readings` (plan §7).

Versionnée (`/v1`), JSON, sans authentification, sur le seul réseau domestique. Chaque réponse
porte `served_at` ; ce qui vient du miroir de l'appareil porte `observed_at`. L'app peut ainsi
s'ouvrir sans attendre le réveil, en disant de quand date ce qu'elle montre.

Le statut se dérive de la base (disponibilité via les indisponibilités ouvertes, écart d'horloge,
liaison cloud via les derniers corps de ports) et d'un état vif léger (démarrage, palier).
"""
from __future__ import annotations

import shutil
import statistics
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel

from ..config import Config
from ..state import RuntimeState
from ..store import Store

GRANDEURS = ("mslux", "mstmp", "msrhu", "mssnd")


class Correction(BaseModel):
    field: str          # bedtime | risetime
    value: float        # epoch (référentiel collecteur / NTP)


def _resume_nuit(store: Store, nuit: dict) -> dict:
    """Résumé calculé à la demande (jamais stocké) : durée, couverture, min/moy/max par grandeur."""
    debut, fin = nuit.get("bedtime"), nuit.get("risetime")
    resume = {"duree_s": (fin - debut) if (debut and fin) else None, "grandeurs": {}}
    if not (debut and fin):
        return resume
    points = store.readings_between(debut, fin)
    resume["couverture"] = len(points)
    for g in GRANDEURS:
        vals = [p[g] for p in points if p[g] is not None]
        if vals:
            resume["grandeurs"][g] = {"min": min(vals), "moy": round(statistics.mean(vals), 2),
                                      "max": max(vals)}
    return resume


def _status(store: Store, cfg: Config, state: RuntimeState) -> dict:
    maintenant = time.time()
    ouvertes = store.open_outages()
    dernier = store.latest_reading()
    horloge = store.latest_clock_check()
    backend = store.last_port_body("backend") or {}
    transport = store.last_port_body("transport") or {}
    device = store.last_port_body("device") or {}
    hb = store.last_heartbeat()

    ecart = horloge.get("offset_time_s") if horloge else None
    du = shutil.disk_usage(Path(store.path).parent)

    return {
        "served_at": maintenant,
        "reveil": {
            "joignable": not ouvertes,
            "cause_indisponibilite": ouvertes[0]["cause"] if ouvertes else None,
            "depuis": ouvertes[0]["start"] if ouvertes else None,
            "adresse_connue": state.reveil_host,
            "dernier_releve_at": dernier["ts"] if dernier else None,
        },
        "horloge": {
            "ecart_s": ecart,
            "seuil_s": cfg.horloge.seuil_ecart_s,
            "au_dela_du_seuil": ecart is not None and abs(ecart) > cfg.horloge.seuil_ecart_s,
            "correction": "impossible : l'heure du réveil ne s'écrit pas",
            "mesure_at": horloge.get("ts") if horloge else None,
            "observed_at": horloge.get("device_time") if horloge else None,
        },
        "cloud": {
            "dcs_state": backend.get("dcs-state"),
            "lastsignon": backend.get("lastsignon"),
            "transport_state": transport.get("state"),
            "allowuploads": device.get("allowuploads"),
            "url": backend.get("url"),
        },
        "collecteur": {
            "demarre_at": state.started_at,
            "dernier_battement_at": hb,
            "cadence_wusrd_s": state.cadence_wusrd_s,
        },
        "disque": {
            "total": du.total,
            "libre": du.free,
            "base_octets": Path(store.path).stat().st_size if Path(store.path).exists() else 0,
        },
        "indisponibilites_ouvertes": ouvertes,
    }


def create_app(store: Store, cfg: Config, state: RuntimeState | None = None) -> FastAPI:
    state = state or RuntimeState()
    app = FastAPI(title="Somneo-Scraper — collecteur", version="v1")

    @app.get("/v1/status")
    def status() -> dict:
        return _status(store, cfg, state)

    @app.get("/v1/readings")
    def readings(
        from_: float = Query(0.0, alias="from"),
        to: float = Query(default_factory=time.time),
        limit: int = Query(100000, le=500000),
    ) -> dict:
        points = store.readings_between(from_, to, limit)
        return {"served_at": time.time(), "from": from_, "to": to,
                "count": len(points), "readings": points}

    @app.get("/v1/nights")
    def nights(from_: float = Query(0.0, alias="from"),
               to: float = Query(default_factory=time.time)) -> dict:
        rows = store.list_nights(from_, to)
        for n in rows:
            n["resume"] = _resume_nuit(store, n)
        return {"served_at": time.time(), "count": len(rows), "nights": rows}

    @app.get("/v1/nights/{night_id}")
    def night(night_id: int) -> dict:
        n = store.get_night(night_id)
        if n is None:
            raise HTTPException(404, "nuit inconnue")
        n["resume"] = _resume_nuit(store, n)
        n["served_at"] = time.time()
        return n

    @app.post("/v1/nights/{night_id}/corrections")
    def corriger(night_id: int, c: Correction) -> dict:
        n = store.get_night(night_id)
        if n is None:
            raise HTTPException(404, "nuit inconnue")
        if c.field not in ("bedtime", "risetime"):
            raise HTTPException(422, "champ corrigible : bedtime ou risetime")
        # un lever antérieur au coucher est refusé, valeur précédente conservée (cadrage §F)
        bedtime = c.value if c.field == "bedtime" else n.get("bedtime")
        risetime = c.value if c.field == "risetime" else n.get("risetime")
        if bedtime and risetime and risetime < bedtime:
            raise HTTPException(422, "le lever ne peut pas précéder le coucher")
        store.add_night_correction(night_id, c.field, c.value)
        return {"served_at": time.time(), "night": store.get_night(night_id)}

    return app
