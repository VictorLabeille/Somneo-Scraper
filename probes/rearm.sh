#!/bin/bash
# Rearme le lot. Depose sur la carte pour que la ligne de commande SSH ne contienne pas le
# motif recherche : un pkill -f depuis SSH tue sa propre session, erreur faite deux fois.
cd "$HOME/somneo-dev" || exit 1
for p in $(pgrep -f "lot_matinal"); do
  [ "$p" = "$$" ] || [ "$p" = "$PPID" ] || kill "$p" 2>/dev/null
done
sleep 1
[ -f balayage-wu.jsonl ] && mv -f balayage-wu.jsonl balayage-wu3.jsonl
chmod +x lot_matinal.sh
setsid nohup ./lot_matinal.sh < /dev/null > /dev/null 2>&1 &
sleep 2
echo "lot rearme"
