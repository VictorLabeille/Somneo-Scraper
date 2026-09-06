#!/bin/bash
# Lot autonome du 7 septembre 2026 — 08:00 a 16:00, personne n'est la.
#
# L'heure se compare en SECONDES DEPUIS L'EPOQUE, jamais en texte : "2240" < "0730" est faux
# en comparaison de chaines, et c'est ce qui a coupe la capture le 6 au soir.
# Le superviseur relance la capture des que ce lot a fini : rien a ordonnancer ici.
cd "$HOME/somneo-dev" || exit 1
exec >> lot-matinal.log 2>&1

CIBLE=$(date -d "2026-09-07 08:00" +%s)
FIN=$(date -d "2026-09-07 16:00" +%s)
echo "=== lot arme le $(date '+%F %T'), declenchement $(date -d "@$CIBLE" '+%F %T') ==="
while [ "$(date +%s)" -lt "$CIBLE" ]; do sleep 60; done

echo "=== $(date '+%F %T') : arret propre de la capture ==="
PID=$(pgrep -x -f "python3 capture.py" | head -1)
[ -n "$PID" ] && kill -TERM "$PID" && sleep 3

echo "=== $(date '+%F %T') : 1/7 balayage wu + 3 lettres (reprise) ==="
BALAYAGE_PREFIXE=wu BALAYAGE_LETTRES=3 python3 -u balayage.py

echo "=== $(date '+%F %T') : 2/7 balayage exhaustif 3 lettres, sans prefixe ==="
BALAYAGE_PREFIXE= BALAYAGE_LETTRES=3 python3 -u balayage.py

echo "=== $(date '+%F %T') : 3/7 surface de l API ==="
python3 -u surface.py

echo "=== $(date '+%F %T') : 4/7 TLS et corps invalides ==="
python3 -u tls.py

echo "=== $(date '+%F %T') : 5/7 pysomneo sous concurrence ==="
./.venv/bin/python -u crash_concurrence.py

echo "=== $(date '+%F %T') : 6/7 escalade de cadence ==="
python3 -u cadence.py

echo "=== $(date '+%F %T') : 7/7 balayage exhaustif 4 lettres, borne a 16:00 ==="
BALAYAGE_PREFIXE= BALAYAGE_LETTRES=4 BALAYAGE_FIN=$FIN python3 -u balayage.py

echo "=== $(date '+%F %T') : lot termine ==="
