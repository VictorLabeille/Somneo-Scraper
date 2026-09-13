# collector/ — le collecteur

Serveur local (Python + FastAPI) qui interroge le Philips Somneo en continu et historise dans
SQLite. Back-end de SleepMaxxer. **Le fonctionnel et les décisions sont dans les specs**
(`.claude/specs/2026-09-12-plan-technique-collecteur.md`), pas ici.

## État

**Incréments 1, 2, 3 écrits et testés hors appareil** : collecte, nuits (machine à états,
`/v1/nights`), rattrapage (`/v1/sync`), catalogue, mDNS. L'incrément 5 (surveillance d'horloge)
est de fait couvert par l'incrément 1. **Incrément 4 (relais du pilotage) écrit** : gestes de
nuit (`POST /v1/nights/bedtime|risetime`, appui retenu puis rejoué si le réveil ne répond pas),
lumière, veilleuse, coucher de soleil (marche/arrêt et réglages), rappel, alarmes (lecture,
création, édition, masquage), et le miroir `GET /v1/device`. Le départ en douceur (`sndss`) a été
mesuré sur l'appareil (`probes/sndss.py`, 2026-09-13) et est relayé en numéro brut ; son sens
physique reste inconnu, à ne pas surfacer côté app avant de l'établir. 108 tests. **Reste** le
critère du plan §10 pour chaque incrément déployé (*tourne sept jours*, etc.), qui se constate
après déploiement sur la carte, et — critère propre à l'incrément 4 — la validation *sur
l'appareil* qu'aucune écriture n'a d'effet non demandé (le faux réveil ne peut pas la donner).

## Développer et tester (hors appareil)

Aucun accès au réveil n'est requis : les tests tournent contre un faux réveil HTTPS.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pip install "git+file://$HOME/_PROJETS/pysomneo@13ec0c5"   # la 6.0 async épinglée (plan §8)
pytest
```

Toujours le commit `13ec0c5`, jamais le clone tel quel : il est sur `master`, la 5.0.6
synchrone, dont l'adaptateur réessaie un `500` douze fois (plan §2). Le collecteur a été écrit
par erreur contre elle jusqu'au 2026-09-13.

Le test cardinal (`tests/test_gateway.py`) vérifie l'invariant qui tient tout : sous n'importe
quelle charge, la passerelle n'a **jamais deux requêtes en vol**. `tests/test_isolation.py`
vérifie que **seul `gateway.py` importe `pysomneo`**.

## Déployer sur la carte

```bash
sudo ./deploy/install.sh      # crée l'utilisateur, le venv, installe le service (sans le démarrer)
```

Le service n'est pas démarré automatiquement : le lancer par-dessus `capture.py` ouvrirait une
seconde connexion au réveil. La bascule se fait à la main, dans l'ordre — `install.sh` l'affiche.

## Structure

```
src/somneo_collector/
  __main__.py   un seul processus, un seul worker uvicorn, superviseur de découverte
  config.py     cadences, seuils, chemins (surcharge par TOML hors dépôt)
  gateway.py    SEUL module qui importe pysomneo (6.0 async) — verrou, espacement, une requête
                en vol ; écritures pysomneo cache vidé
  discovery.py  SSDP (réveil) + mDNS (collecteur)
  store/        schema.sql + accès base (WAL, seq global)
  collect.py    planificateur des lectures ; indisponibilités datées ; rejeu des gestes retenus
  nights.py     machine à états des nuits, partagée par la collecte et le relais
  relay.py      relais du pilotage : bornes, écrire → relire → confirmer, gestes de nuit
  clock.py      dérive de l'horloge, lecture seule (l'heure ne s'écrit pas)
  backup.py     sauvegarde en rotation
  state.py      état vif (démarrage, palier, adresse du réveil)
  api/          routes FastAPI : lectures, rattrapage, miroir, écritures du relais
```
