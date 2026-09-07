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
#
# La liste des sondes se DEDUIT du dossier, elle ne s'ecrit pas. Une liste en dur a coute
# 23 minutes de mesure le 7 septembre 2026 : `surface.py` et `tls.py`, ecrites apres elle,
# n'y figuraient pas. Le superviseur les a crus absentes, a relance la capture par-dessus, et
# les deux clients concurrents sont tombes ensemble — l'appareil ne sert qu'une connexion.
# Recalculee a chaque tour, la liste couvre d'office toute sonde deposee depuis.
cd "$HOME/somneo-dev" || exit 1
exec >> superviseur.log 2>&1
echo "=== superviseur demarre $(date '+%F %T') ==="

while true; do
  # Tout .py du dossier sauf la capture elle-meme. `paste -sd'|'` en fait une alternative.
  SONDES=$(ls *.py 2>/dev/null | grep -vx 'capture.py' | sed 's/\.py$//' | paste -sd'|')

  if [ -n "$SONDES" ] && pgrep -f "($SONDES)\.py" | grep -qv "^$$\$"; then
    :   # une sonde travaille : on laisse l'appareil tranquille
  elif ! pgrep -x -f "python3 capture.py" > /dev/null; then
    echo "$(date '+%F %T') : capture absente, relance"
    setsid nohup python3 capture.py < /dev/null >> capture.log 2>&1 &
  fi
  sleep 30
done
