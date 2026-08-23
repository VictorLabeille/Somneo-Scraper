#!/bin/sh
# Identifie la broche de la LED verte de la Radxa Zero.
#
# Usage (en root, SUR LA CARTE, en vue de la LED) :
#     scp radxa-config/led-probe.sh radxa:
#     ssh -t radxa 'sudo ~/led-probe.sh'
#
# Outil de diagnostic, a ressortir si la LED cesse de repondre ou si le script
# tourne un jour sur une autre revision de carte : il force successivement les
# lignes 10 puis 8 du domaine AO et laisse l'observateur dire laquelle eteint la
# LED. La ligne qui ne pilote pas la LED part vers le header 40 points, ou rien
# n'est branche : les deux essais sont sans consequence.
#
# Sur la carte en service (v1.51+), c'est la ligne 10 -- voir led-schedule.sh.
#
# Armbian nomme les lignes GPIO d apres le numero de broche du header 40 points
# (ex. "35 [GPIOAO_8]"), donc gpioset ne les trouve pas sous leur nom SoC. Les
# broches non reliees au header restent "unnamed" : c est le cas de GPIOAO_10
# sur les revisions v1.51+, justement parce qu elle pilote la LED.
set -u

echo "=== chips GPIO ==="
gpiodetect

AO=$(gpiodetect | grep -iE "ao" | head -1 | cut -d" " -f1)
[ -n "$AO" ] || AO=gpiochip0
echo
echo "chip AO retenu : $AO"
echo
echo "=== lignes 8 a 11 du chip AO ==="
gpioinfo -c "$AO" 2>/dev/null | sed -n "/line *[89]:/p;/line *1[01]:/p"

for OFF in 10 8; do
  echo
  echo ">>> $AO ligne $OFF forcee a 0 pendant 8 s --- REGARDE LA LED VERTE"
  timeout 8 gpioset -c "$AO" "$OFF=0"; rc=$?
  if [ $rc -ne 124 ] && [ $rc -ne 0 ]; then
    echo "    ECHEC (code $rc) : ligne occupee par un pilote ?"
  else
    echo "    ligne relachee --- la LED s est-elle rallumee ?"
  fi
  sleep 4
done

echo
echo "=== termine : quelle ligne a eteint la LED ? ==="
