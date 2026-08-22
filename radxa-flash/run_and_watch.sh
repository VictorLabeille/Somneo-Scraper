#!/bin/bash
# Boot maskrom via WSL/usbip, detachement immediat, puis observation exhaustive
# de TOUT ce qui apparait sur l'USB (cote WSL et cote Windows) pendant 3 min.
#
# Objectif : identifier ce que devient le board apres [BL2 END] — UMS (1b8e:2200),
# retour en maskrom (1b8e:c003), autre VID:PID, ou plus rien du tout.

BASE=/home/victor/_PROJETS/Somneo-Scraper/radxa-flash
LOG=$BASE/watch.log
PS='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
: > "$LOG"

log(){ echo "[$(date +%H:%M:%S.%3N)] $*" | tee -a "$LOG"; }
ps_run(){ "$PS" -NoProfile -Command "$1" 2>&1 | tr -d '\r'; }

dmesg -C 2>/dev/null

log "Phase 1 : attente du debranchement du board"
for i in $(seq 1 1200); do
  ps_run "usbipd list" | grep -qE "1b8e:|0000:0002" || { log "board absent"; break; }
  sleep 0.5
done

log "Phase 2 : attente du maskrom (GX-CHIP)"
BUSID=""
for i in $(seq 1 1200); do
  line=$(ps_run "usbipd list" | grep "1b8e:c003" | head -1)
  [ -n "$line" ] && { BUSID=$(echo "$line" | awk '{print $1}'); log "GX-CHIP sur busid $BUSID"; break; }
  sleep 0.4
done
[ -z "$BUSID" ] && { log "TIMEOUT maskrom"; exit 1; }

# L'attachement echoue parfois en zombie : usbipd annonce "Attached" mais le
# peripherique n'apparait jamais dans WSL. On detache et on rejoue.
attached=0
for essai in 1 2 3; do
  log "Attachement $BUSID -> WSL (essai $essai)"
  ps_run "usbipd attach --wsl --busid $BUSID" >/dev/null
  for i in $(seq 1 30); do lsusb -d 1b8e:c003 >/dev/null 2>&1 && { attached=1; break; }; sleep 0.2; done
  [ $attached -eq 1 ] && { log "visible dans WSL"; break; }
  log "  zombie detecte, detachement et nouvel essai"
  ps_run "usbipd detach --busid $BUSID" >/dev/null
  sleep 2
done
[ $attached -eq 1 ] || { log "ERREUR : pas visible dans WSL apres 3 essais"; exit 1; }

log "--- boot-g12.py (loader=${LOADER:-rz-udisk-loader.bin}) ---"
cd "$BASE/pyamlboot"
export AMLBOOT_TIMEOUT=15000 AMLBOOT_RETRIES=4 AMLBOOT_VERBOSE=0
stdbuf -oL timeout 150 python3 -u boot-g12.py "$BASE/${LOADER:-rz-udisk-loader.bin}" 2>&1 \
  | stdbuf -oL grep -vE "Deprecation|import pkg" \
  | while IFS= read -r l; do echo "[$(date +%H:%M:%S.%3N)] $l" | tee -a "$LOG"; done

log "Detachement immediat de $BUSID"
ps_run "usbipd detach --busid $BUSID" >/dev/null

# --- Observation exhaustive -------------------------------------------------
log "=== OBSERVATION 180 s : tout evenement USB, WSL + Windows ==="
prev_win=""
prev_wsl=""
for i in $(seq 1 180); do
  win=$(ps_run "usbipd list" | grep -E "^[0-9]+-[0-9]+" | awk '{print $1, $2}' | tr '\n' ';')
  wsl=$(lsusb 2>/dev/null | grep -v "1d6b:000" | awk '{print $6}' | tr '\n' ';')
  disk=$(ps_run "Get-Disk | Where-Object BusType -eq 'USB' | Select-Object -ExpandProperty FriendlyName" | tr '\n' ';')

  if [ "$win" != "$prev_win" ]; then
    log "  t=${i}s  WINDOWS change -> ${win:-<vide>}"
    prev_win="$win"
  fi
  if [ "$wsl" != "$prev_wsl" ]; then
    log "  t=${i}s  WSL change -> ${wsl:-<vide>}"
    prev_wsl="$wsl"
  fi
  if [ -n "$disk" ]; then
    log "  t=${i}s  >>> DISQUE USB : $disk <<<"
    ps_run "Get-Disk | Where-Object BusType -eq 'USB' | Format-List Number,FriendlyName,Size,PartitionStyle,OperationalStatus" \
      | while IFS= read -r l; do log "    $l"; done
    break
  fi
  sleep 1
done

log "=== dmesg final ==="
dmesg 2>&1 | tail -40 | tee -a "$LOG"
log "=== entrees PnP 1b8e presentes ==="
ps_run "Get-PnpDevice -PresentOnly | Where-Object { \$_.InstanceId -like '*VID_1B8E*' } | Select-Object Status,Class,FriendlyName,InstanceId | Format-List" \
  | while IFS= read -r l; do log "  $l"; done
