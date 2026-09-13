"""Collecteur local du Philips Somneo — back-end de SleepMaxxer.

Un seul processus : l'API FastAPI et les tâches de fond (collecte, horloge, sauvegarde,
découverte). Un seul accès au réveil, `gateway.DeviceGateway`, seul module à importer
`pysomneo`. Voir `.claude/specs/2026-09-12-plan-technique-collecteur.md`.
"""

__version__ = "0.1.0"
