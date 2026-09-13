# collector/ — le collecteur

Serveur local (Python + FastAPI) qui interroge le Philips Somneo en continu et historise dans
SQLite. Back-end de SleepMaxxer. **Le fonctionnel et les décisions sont dans les specs**
(`.claude/specs/2026-09-12-plan-technique-collecteur.md`), pas ici.

## État

**Incrément 1 (collecte) écrit, testé hors appareil.** Reste, avant de le déclarer fini, son
critère du plan §10 : *tourne sept jours sans intervention, la capture arrêtée* — ce qui, par
nature, se constate après déploiement.

## Développer et tester (hors appareil)

Aucun accès au réveil n'est requis : les tests tournent contre un faux réveil HTTPS.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[test]'
pip install ../pysomneo          # le fork épinglé (ou PyPI quand la 6.0.0 sera publiée)
pytest
```

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
  gateway.py    SEUL module qui importe pysomneo — verrou, espacement, une requête en vol
  discovery.py  SSDP (réveil) + mDNS (collecteur)
  store/        schema.sql + accès base (WAL, seq global)
  collect.py    planificateur des lectures ; indisponibilités datées
  clock.py      dérive de l'horloge, lecture seule (l'heure ne s'écrit pas)
  backup.py     sauvegarde en rotation
  api/          routes FastAPI (/v1/status, /v1/readings)
```
