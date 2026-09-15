"""Routes de l'API (plan §7).

Versionnée (`/v1`), JSON, sans authentification, sur le seul réseau domestique. Chaque réponse
porte `served_at` ; ce qui vient du miroir de l'appareil porte `observed_at`. L'app peut ainsi
s'ouvrir sans attendre le réveil, en disant de quand date ce qu'elle montre.

Le statut se dérive de la base (disponibilité via les indisponibilités ouvertes, écart d'horloge,
liaison cloud via les derniers corps de ports) et d'un état vif léger (démarrage, palier).

Les écritures (incrément 4) passent toutes par le relais (`relay.py`) : bornes, passerelle,
relecture, confirmation. Ces routes ne font que traduire son résultat en réponse HTTP.
"""
from __future__ import annotations

import shutil
import statistics
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, StrictBool, StrictInt

from ..config import Config
from ..nights import NightTracker
from ..relay import Relay, Resultat
from ..state import RuntimeState
from ..store import Store

GRANDEURS = ("mslux", "mstmp", "msrhu", "mssnd")
# Les ports que sert le miroir (plan §7), tels que la collecte les relève (§4).
PORTS_MIROIR = ("wulgt", "wudsk", "wualm", "wualm/aenvs", "wualm/aalms", "wuply", "wusts",
                "wungt")


class Correction(BaseModel):
    field: str                  # bedtime | risetime
    value: float | None         # epoch (référentiel collecteur / NTP) ; null : retour au relevé


class Lampe(BaseModel):
    on: StrictBool
    level: StrictInt | None = None     # échelle de l'appareil ; bornes dans relay.py


class Interrupteur(BaseModel):
    on: StrictBool


class Rappel(BaseModel):
    minutes: StrictInt


class SunsetSettings(BaseModel):
    """Réglages du coucher de soleil, tous optionnels ; numéros bruts (comme les alarmes)."""

    durat: StrictInt | None = None         # durée (5-60 min)
    curve: StrictInt | None = None         # intensité (0-25 : un coucher peut être éteint)
    ctype: StrictInt | None = None         # thème lumineux
    snddv: str | None = None
    sndch: str | None = None
    sndlv: StrictInt | None = None         # volume (1-25)
    sndss: StrictInt | None = None         # départ en douceur, numéro brut non borné (sens inconnu)


class PowerWake(BaseModel):
    on: StrictBool
    delta: StrictInt | None = None     # minutes après l'heure de l'alarme (1-59) ; bornes : relay


class AlarmEdit(BaseModel):
    """Champs éditables d'un profil, tous optionnels. Numéros bruts de l'appareil pour thème
    (`ctype`), source (`snddv`) et canal (`sndch`) : l'app les tient de /v1/catalog/themes."""

    enabled: StrictBool | None = None
    hour: StrictInt | None = None
    minute: StrictInt | None = None
    days: StrictInt | None = None          # masque `daynm` (bit 1 = lundi … bit 7 = dimanche)
    ctype: StrictInt | None = None         # thème lumineux du lever
    curve: StrictInt | None = None         # intensité du lever (1-25)
    durat: StrictInt | None = None         # durée du lever (5-40 min)
    snddv: str | None = None               # source sonore (ex. "wus", "fmr", "off")
    sndch: str | None = None               # canal / piste
    sndlv: StrictInt | None = None         # volume (1-25)
    sndss: StrictInt | None = None         # départ en douceur, numéro brut non borné (sens inconnu)
    powerwake: PowerWake | None = None


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


def _alarmes(aenvs: dict, aalms: dict) -> tuple[list[dict], list[int]]:
    """Décode `aenvs`/`aalms` — tableaux par profil, dans la forme que lit `pysomneo`
    (`util.alarms_to_dict`) — en alarmes visibles, et relève les masquées mais armées :
    l'anomalie du cadrage §3.D, aucune alarme masquée ne doit pouvoir sonner."""
    prfen, prfvs, pwrsv = (aenvs.get(k) or [] for k in ("prfen", "prfvs", "pwrsv"))
    almhr, almmn, daynm = (aalms.get(k) or [] for k in ("almhr", "almmn", "daynm"))

    def champ(liste: list, i: int):
        return liste[i] if i < len(liste) else None

    visibles, masquees_armees = [], []
    for i, active in enumerate(prfen):
        n = i + 1
        visible = bool(champ(prfvs, i))
        if active and not visible:
            masquees_armees.append(n)
        if not visible:
            continue
        pw = pwrsv[3 * i:3 * i + 3]
        visibles.append({
            "n": n, "enabled": bool(active),
            "hour": champ(almhr, i), "minute": champ(almmn, i), "days": champ(daynm, i),
            "powerwake": ({"on": bool(pw[0]), "hour": pw[1], "minute": pw[2]}
                          if len(pw) == 3 else None),
        })
    return visibles, masquees_armees


def _device(store: Store) -> dict:
    """Le miroir (plan §7) : le dernier corps de chaque port, daté, et deux décodages — `wusts`
    en bits, les alarmes visibles. Rien n'est interprété au-delà : le collecteur sert des mesures."""
    ports = {p: store.last_port(p) for p in PORTS_MIROIR}
    corps = {p: (v or {}).get("body") or {} for p, v in ports.items()}
    visibles, masquees = _alarmes(corps["wualm/aenvs"], corps["wualm/aalms"])
    wusts = corps["wusts"].get("wusts")
    return {
        "served_at": time.time(),
        "ports": ports,
        "wusts_bits": [b for b in range(16) if isinstance(wusts, int) and wusts >> b & 1],
        "alarms": visibles,
        "hidden_armed_alarms": masquees,
    }


def _status(store: Store, cfg: Config, state: RuntimeState) -> dict:
    maintenant = time.time()
    ouvertes = store.open_outages()
    dernier = store.latest_reading()
    horloge = store.latest_clock_check()
    backend = store.last_port_body("backend") or {}
    transport = store.last_port_body("transport") or {}
    device = store.last_port_body("device") or {}
    hb = store.last_heartbeat()
    _, masquees_armees = _alarmes(store.last_port_body("wualm/aenvs") or {},
                                  store.last_port_body("wualm/aalms") or {})

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
        "alarmes": {"masquees_armees": masquees_armees},
        "collecteur": {
            "demarre_at": state.started_at,
            "dernier_battement_at": hb,
            "cadence_wusrd_s": state.cadence_wusrd_s,
            # l'heure de la carte est-elle synchronisée (NTP) ? Signalée, jamais corrigée (écart
            # 5) ; None quand elle n'est pas surveillée
            "heure_synchronisee": cfg.synchro_ntp.exists() if cfg.synchro_ntp else None,
        },
        "disque": {
            "total": du.total,
            "libre": du.free,
            "base_octets": Path(store.path).stat().st_size if Path(store.path).exists() else 0,
        },
        "indisponibilites_ouvertes": ouvertes,
    }


def create_app(store: Store, cfg: Config, state: RuntimeState | None = None,
               relay: Relay | None = None) -> FastAPI:
    """`relay` porte la passerelle (`gateway_getter`) et la machine des nuits partagée avec la
    collecte. Sans lui (tests des lectures), un relais sans passerelle : toute écriture → 503."""
    state = state or RuntimeState()
    relay = relay or Relay(store, NightTracker(store), lambda: None)
    app = FastAPI(title="Somneo-Scraper — collecteur", version="v1")
    app.state.relay = relay

    def repondre(res: Resultat) -> JSONResponse:
        return JSONResponse(status_code=res.status, content=res.body)

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
        # `null` revient au relevé (écarts 1-3) ; sans correction en vigueur, rien à annuler, et
        # rien d'écrit : un renvoi après une coupure réseau ne doit pas échouer
        if c.value is None and n[f"{c.field}_origin"] != "corrected":
            return {"served_at": time.time(), "night": n}
        nouvelle = c.value if c.value is not None else n[f"{c.field}_observed"]
        # un lever antérieur au coucher est refusé, valeur précédente conservée (cadrage §F)
        bedtime = nouvelle if c.field == "bedtime" else n.get("bedtime")
        risetime = nouvelle if c.field == "risetime" else n.get("risetime")
        if bedtime and risetime and risetime < bedtime:
            raise HTTPException(422, "le lever ne peut pas précéder le coucher")
        store.add_night_correction(night_id, c.field, c.value)
        return {"served_at": time.time(), "night": store.get_night(night_id)}

    @app.get("/v1/sync")
    def sync(before: float | None = None, since_seq: int | None = None,
             limit: int = Query(500, le=5000)) -> dict:
        """Rattrapage (§7). `since_seq` : tout ce qui a changé depuis, dans l'ordre. `before` :
        les nuits récentes d'un jour donné, avec leurs points et agrégats, du plus récent au
        plus ancien. Le collecteur ne tient aucun état du client : même demande, même contenu.

        Dans les deux modes, un agrégat porte son type (temp, hum, snd, lux) sous `aggregate_kind`
        — en `since_seq`, `kind` est le genre de l'élément (écart 4) — et `hist` est une chaîne
        JSON, pas un objet : l'app la range telle quelle (écart 11). Une nuit est servie comme par
        `GET /v1/nights/{id}` : heure qui fait foi, relevé à côté, corrections (écarts 1-3)."""
        base = {"served_at": time.time(), "current_seq": store.seq_courant()}
        if since_seq is not None:
            items = store.changes_since(since_seq, limit)
            return {**base, "mode": "since_seq", "since_seq": since_seq,
                    "count": len(items), "items": items}
        if before is not None:
            nuits = store.nights_before(before, limit)
            for n in nuits:
                if n.get("bedtime") and n.get("risetime"):
                    n["readings"] = store.readings_between(n["bedtime"], n["risetime"])
                    n["aggregates"] = store.aggregates_between(n["bedtime"], n["risetime"])
            return {**base, "mode": "before", "before": before, "count": len(nuits),
                    "nights": nuits}
        raise HTTPException(422, "préciser before=<epoch> ou since_seq=<n>")

    @app.get("/v1/catalog/themes")
    def catalog_themes() -> dict:
        """Numéros → noms des thèmes et sons, avec leur source. Ici : l'appareil (ports files/*)."""
        out: dict[str, dict] = {}
        for port in ("files/wakeup", "files/lightthemes", "files/dusklightthemes",
                     "files/winddowndusk"):
            corps = store.last_port_body(port) or {}
            out[port.split("/", 1)[1]] = {"source": "appareil",
                                          "themes": {k: v for k, v in corps.items()}}
        return {"served_at": time.time(), "catalog": out}

    @app.get("/v1/device")
    def device() -> dict:
        return _device(store)

    # ---- écritures : le relais du pilotage (incrément 4) ---------------------------------
    @app.post("/v1/nights/bedtime")
    async def bedtime() -> JSONResponse:
        return repondre(await relay.bedtime())

    @app.post("/v1/nights/risetime")
    async def risetime() -> JSONResponse:
        return repondre(await relay.risetime())

    @app.put("/v1/light")
    async def light(cmd: Lampe) -> JSONResponse:
        return repondre(await relay.light(cmd.on, cmd.level))

    @app.put("/v1/nightlight")
    async def nightlight(cmd: Interrupteur) -> JSONResponse:
        return repondre(await relay.nightlight(cmd.on))

    @app.put("/v1/sunset")
    async def sunset(cmd: Interrupteur) -> JSONResponse:
        return repondre(await relay.sunset(cmd.on))

    @app.put("/v1/sunset/settings")
    async def sunset_settings(cmd: SunsetSettings) -> JSONResponse:
        return repondre(await relay.sunset_settings(cmd.model_dump(exclude_none=True)))

    @app.put("/v1/snooze")
    async def snooze(cmd: Rappel) -> JSONResponse:
        return repondre(await relay.snooze(cmd.minutes))

    @app.get("/v1/alarms/{n}")
    async def alarm(n: int) -> JSONResponse:
        return repondre(await relay.get_alarm(n))

    @app.post("/v1/alarms")
    async def creer_alarme() -> JSONResponse:
        return repondre(await relay.create_alarm())

    @app.put("/v1/alarms/{n}")
    async def modifier_alarme(n: int, cmd: AlarmEdit) -> JSONResponse:
        return repondre(await relay.set_alarm(n, cmd.model_dump(exclude_none=True)))

    @app.delete("/v1/alarms/{n}")
    async def supprimer_alarme(n: int) -> JSONResponse:
        return repondre(await relay.delete_alarm(n))

    return app
