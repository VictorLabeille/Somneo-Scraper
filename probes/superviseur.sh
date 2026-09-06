#!/bin/bash
# Superviseur de la capture — tourne en continu.
#
# La capture est la seule chose qui ne se rattrape pas : une nuit perdue est perdue. Elle a
# ete coupee deux fois ce soir par des lancements de sondes. Plutot qu'un enchaînement fragile,
# une regle simple appliquee toutes les 30 s :
#
#   si aucune sonde longue ne tourne ET que la capture est absente -> la relancer.
#
# Consequence : n'importe quelle sonde peut arreter la capture proprement pour travailler ;
# elle repart toute seule des que la sonde a fini. Aucun ordonnancement a maintenir.
cd "$HOME/somneo-dev" || exit 1
exec >> superviseur.log 2>&1
echo "=== superviseur demarre $(date '+%F %T') ==="

SONDES="balayage.py|crash_concurrence.py|cadence.py|exploration.py|etats_et_udp.py|repos.py|marche.py|concurrence.py|chevauchement.py|bornes_brght.py"

while true; do
  if pgrep -f "$SONDES" | grep -qv "^$$\$"; then
    :   # une sonde travaille : on laisse l'appareil tranquille
  elif ! pgrep -x -f "python3 capture.py" > /dev/null; then
    echo "$(date '+%F %T') : capture absente, relance"
    setsid nohup python3 capture.py < /dev/null >> capture.log 2>&1 &
  fi
  sleep 30
done
