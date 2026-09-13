#!/usr/bin/env bash
# Installe le collecteur sur la carte. À lancer en root, DEPUIS le dossier collector/.
#
# N'ACTIVE PAS le service tout seul : le démarrer par-dessus capture.py, ce serait deux clients
# vers un réveil qui n'en sert qu'un, et l'appareil ferait tomber les deux (plan §8). La bascule
# depuis la capture se fait à la main, dans l'ordre, et ce script l'affiche à la fin.
set -euo pipefail

PREFIX=/opt/somneo-collector
STATE=/var/lib/somneo-collector
CONF=/etc/somneo-collector
# pysomneo 6.0 async, épinglée au commit 13ec0c5 du fork (365d313 + limit=1, PR #26) — plan §0,
# §8. Une archive par SHA complet : pas besoin de git sur la carte. Ni le clone local (sur master)
# ni PyPI tant que la 6.0.0 n'est pas publiée : la 5.0.6 réessaie un 500 douze fois (plan §2).
PYSOMNEO="${PYSOMNEO_SPEC:-https://github.com/VictorLabeille/pysomneo/archive/13ec0c59c394de8aad943c5ced4f7b39f584790a.tar.gz}"

[ "$(id -u)" -eq 0 ] || { echo "à lancer en root" >&2; exit 1; }

id somneo &>/dev/null || useradd --system --home "$STATE" --shell /usr/sbin/nologin somneo
install -d -o somneo -g somneo "$STATE" "$STATE/backups"
install -d "$PREFIX" "$CONF"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
"$PREFIX/venv/bin/pip" install "$PYSOMNEO"
"$PREFIX/venv/bin/pip" install .

install -m 644 deploy/somneo-collector.service /etc/systemd/system/somneo-collector.service
systemctl daemon-reload
systemctl enable somneo-collector.service     # activé au boot, mais PAS démarré maintenant

cat <<'FIN'

Installé. Le service est activé au boot mais n'est PAS démarré : il ne faut pas le lancer
par-dessus capture.py. Bascule depuis la capture, dans l'ordre (plan §8) :

  1. arrêter le superviseur PAR SON PID :   kill <pid de superviseur.sh>
  2. arrêter la capture PAR SON PID :        kill <pid de capture.py>   (SIGTERM, elle le gère)
  3. démarrer le collecteur :                systemctl start somneo-collector

  Jamais de pkill -f / pgrep -f distant portant le nom de la cible (AGENTS.md).
  Retour arrière : systemctl stop somneo-collector, puis relancer le superviseur.

  Config optionnelle : /etc/somneo-collector/config.toml (surcharge les défauts).
FIN
