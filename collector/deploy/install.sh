#!/usr/bin/env bash
# Installe le collecteur sur la carte. À lancer en root, DEPUIS le dossier collector/.
#
# N'ACTIVE PAS le service tout seul : le démarrer par-dessus capture.py, ce serait deux clients
# vers un réveil qui n'en sert qu'un, et l'appareil ferait tomber les deux (plan §8). La bascule
# depuis la capture se fait à la main, dans l'ordre, et ce script l'affiche à la fin. Sur une mise
# à jour — service déjà actif —, il dit seulement de redémarrer.
set -euo pipefail

PREFIX=/opt/somneo-collector
STATE=/var/lib/somneo-collector
CONF=/etc/somneo-collector
# pysomneo 6.0 async, épinglée au commit 13ec0c5 du fork (365d313 + limit=1, PR #26) — plan §0,
# §8. Une archive par SHA complet : pas besoin de git sur la carte. Ni le clone local (sur master)
# ni PyPI tant que la 6.0.0 n'est pas publiée : la 5.0.6 réessaie un 500 douze fois (plan §2).
PYSOMNEO="${PYSOMNEO_SPEC:-https://github.com/VictorLabeille/pysomneo/archive/13ec0c59c394de8aad943c5ced4f7b39f584790a.tar.gz}"

[ "$(id -u)" -eq 0 ] || { echo "à lancer en root" >&2; exit 1; }

# L'état du service AVANT l'installation choisit le message final. Actif : c'est une mise à jour,
# la bascule depuis la capture est faite, il ne reste qu'à redémarrer. Tout autre état (première
# installation, retour arrière vers la capture, service en train de redémarrer) : la procédure de
# bascule, le message prudent — un « redémarrer » par-dessus capture.py ferait deux clients.
if systemctl is-active --quiet somneo-collector.service; then MISE_A_JOUR=1; else MISE_A_JOUR=0; fi

id somneo &>/dev/null || useradd --system --home "$STATE" --shell /usr/sbin/nologin somneo
install -d -o somneo -g somneo "$STATE" "$STATE/backups"
install -d "$PREFIX" "$CONF"

# `ensurepip` peut manquer (Debian minimal le sépare : vérifié absent sur la carte le 2026-09-13).
# `python3 -m venv` échouerait alors. On crée donc le venv SANS pip et on l'amorce par get-pip
# (la carte a Internet). Portable : marche aussi là où ensurepip est présent.
python3 -m venv "$PREFIX/venv" --without-pip
curl -fsS https://bootstrap.pypa.io/get-pip.py | "$PREFIX/venv/bin/python"
"$PREFIX/venv/bin/pip" install --upgrade pip
"$PREFIX/venv/bin/pip" install "$PYSOMNEO"
"$PREFIX/venv/bin/pip" install .

install -m 644 deploy/somneo-collector.service /etc/systemd/system/somneo-collector.service
systemctl daemon-reload
systemctl enable somneo-collector.service     # activé au boot, mais PAS démarré maintenant

if [ "$MISE_A_JOUR" -eq 1 ]; then
    cat <<'FIN'

Mis à jour. Le service tourne encore l'ANCIENNE version : le redémarrer pour charger la nouvelle.

  systemctl restart somneo-collector

  Le service était actif : la bascule depuis la capture est déjà faite, ne pas la refaire.
  Config optionnelle : /etc/somneo-collector/config.toml (surcharge les défauts).
FIN
    exit 0
fi

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
