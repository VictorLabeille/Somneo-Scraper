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
FORK="${PYSOMNEO_FORK:-$HOME/_PROJETS/pysomneo}"   # chemin du fork épinglé (sinon PyPI plus tard)

[ "$(id -u)" -eq 0 ] || { echo "à lancer en root" >&2; exit 1; }

id somneo &>/dev/null || useradd --system --home "$STATE" --shell /usr/sbin/nologin somneo
install -d -o somneo -g somneo "$STATE" "$STATE/backups"
install -d "$PREFIX" "$CONF"

python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install --upgrade pip
# pysomneo depuis le fork épinglé tant que la 6.0.0 n'est pas publiée (plan §8) ; sinon PyPI.
if [ -d "$FORK" ]; then
    "$PREFIX/venv/bin/pip" install "$FORK"
else
    echo "fork pysomneo introuvable ($FORK) ; installe pysomneo depuis PyPI" >&2
    "$PREFIX/venv/bin/pip" install pysomneo
fi
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
