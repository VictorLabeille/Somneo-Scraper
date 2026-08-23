#!/bin/sh
# Installe l'extinction nocturne de la LED verte de la Radxa Zero (22 h - 8 h).
#
# Usage (en root, SUR LA CARTE) :
#     scp radxa-config/led-schedule.sh radxa:
#     ssh -t radxa 'sudo ~/led-schedule.sh'
#
# Idempotent : reecrit ses fichiers et relance les unites a chaque execution. Se
# termine par une demonstration de 12 s (extinction puis rallumage) avant de
# reposer l'etat correspondant a l'heure courante -- se placer en vue de la carte.
#
# Pour changer les horaires : les deux minuteries plus loin (OnCalendar) ET le
# test d'heure de radxa-led-boot.service, qui doivent rester coherents.
#
# La LED est cablee sur la ligne 10 du domaine GPIO AO : sur les revisions v1.51+,
# la broche 38 n'est pas reliee au header 40 points, elle pilote la LED. Aucun
# noeud du device tree ne la declare (/sys/class/leds est vide), seul libgpiod
# peut donc la piloter.
#
# Point cle observe le 23/08/2026 : relacher la ligne ne rallume PAS la LED. La
# broche repasse en entree haute impedance et l'etat "allume" pose au demarrage
# par le bootloader est perdu. Il faut donc tenir la ligne a 1 le jour et a 0 la
# nuit -- d'ou deux services qui se relaient, gpioset maintenant l'etat tant que
# son processus vit.
#
# Rien n'est touche dans le chemin de boot, aucun redemarrage n'est necessaire.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "A lancer en root."; exit 1; }

echo "== 1/5 script de pilotage =="
cat > /usr/local/sbin/radxa-led <<'SCRIPT'
#!/bin/sh
# Tient la LED verte allumee (on) ou eteinte (off). Bloque jusqu'a reception d'un
# signal : c'est ce qui maintient l'etat de la ligne, gpioset la relachant a sa
# sortie.
set -eu

LINE=10   # domaine AO ; broche 38 du header sur les revisions ou elle existe

case "${1:-}" in
  on)  VALUE=1 ;;
  off) VALUE=0 ;;
  *)   echo "usage: ${0##*/} on|off" >&2; exit 2 ;;
esac

# Le numero de gpiochip du domaine AO n'est pas garanti stable d'un demarrage a
# l'autre : on le retrouve par son noeud de device tree (bus AO a ff800000)
# plutot que de figer "gpiochip1".
CHIP=""
for d in /sys/bus/gpio/devices/*/; do
  case "$(readlink -f "$d/of_node" 2>/dev/null)" in
    *ff800000*) CHIP="$(basename "$d")"; break ;;
  esac
done
[ -n "$CHIP" ] || { echo "chip GPIO du domaine AO introuvable" >&2; exit 1; }

exec gpioset -c "$CHIP" -C radxa-led "$LINE=$VALUE"
SCRIPT
chmod 755 /usr/local/sbin/radxa-led

echo "== 2/5 services =="
cat > /etc/systemd/system/radxa-led-day.service <<'UNIT'
[Unit]
Description=LED verte allumee (journee)
Conflicts=radxa-led-night.service
StartLimitIntervalSec=0

[Service]
Type=exec
ExecStart=/usr/local/sbin/radxa-led on
# La ligne peut etre encore tenue par le service oppose au moment de la bascule :
# on retente jusqu'a ce qu'elle soit libre.
Restart=on-failure
RestartSec=2
UNIT

cat > /etc/systemd/system/radxa-led-night.service <<'UNIT'
[Unit]
Description=LED verte eteinte (nuit)
Conflicts=radxa-led-day.service
StartLimitIntervalSec=0

[Service]
Type=exec
ExecStart=/usr/local/sbin/radxa-led off
Restart=on-failure
RestartSec=2
UNIT

echo "== 3/5 minuteries =="
cat > /etc/systemd/system/radxa-led-day.timer <<'UNIT'
[Unit]
Description=Rallume la LED verte a 8 h

[Timer]
OnCalendar=*-*-* 08:00:00

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/radxa-led-night.timer <<'UNIT'
[Unit]
Description=Eteint la LED verte a 22 h

[Timer]
OnCalendar=*-*-* 22:00:00

[Install]
WantedBy=timers.target
UNIT

echo "== 4/5 etat au demarrage =="
# Sans cela, un redemarrage en pleine nuit laisserait la LED allumee jusqu'a 22 h :
# les minuteries ne rattrapent pas un declenchement manque.
cat > /etc/systemd/system/radxa-led-boot.service <<'UNIT'
[Unit]
Description=Etat initial de la LED verte selon l'heure
Wants=time-sync.target
After=time-sync.target

[Service]
Type=oneshot
ExecStart=/bin/sh -c 'h=$(date +%%-H); if [ "$h" -ge 22 ] || [ "$h" -lt 8 ]; then systemctl start radxa-led-night.service; else systemctl start radxa-led-day.service; fi'

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable radxa-led-boot.service radxa-led-day.timer radxa-led-night.timer >/dev/null
systemctl start radxa-led-day.timer radxa-led-night.timer

echo "== 5/5 verification visuelle =="
h=$(date +%-H)
if [ "$h" -ge 22 ] || [ "$h" -lt 8 ]; then ETAT=night; else ETAT=day; fi
systemctl start "radxa-led-$ETAT.service"
echo "   heure $h h -> service radxa-led-$ETAT actif"
sleep 2

echo
echo ">>> bascule NUIT : la LED doit s'ETEINDRE (6 s)"
systemctl start radxa-led-night.service
sleep 6
echo ">>> bascule JOUR : la LED doit se RALLUMER (6 s)"
systemctl start radxa-led-day.service
sleep 6
echo ">>> retour a l'etat correspondant a l'heure : $ETAT"
systemctl start "radxa-led-$ETAT.service"

echo
echo "=== etat final ==="
systemctl is-active radxa-led-day.service radxa-led-night.service | tr '\n' ' '; echo "(day night)"
systemctl list-timers --no-pager radxa-led-\*.timer | head -5
