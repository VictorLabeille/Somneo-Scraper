#!/bin/bash
# Amene le Radxa Zero de maskrom -> mode UMS (eMMC exposee en disque USB).
#
# Principe : le boot bootrom se fait depuis WSL (pyamlboot, logs verbeux), mais
# on DETACHE immediatement apres [BL2 END] pour que la re-enumeration en UMS
# soit prise en charge nativement par Windows. L'auto-attach usbipd capturait le
# device pendant sa re-enumeration et cassait le passage en UMS.

BASE=/home/victor/_PROJETS/Somneo-Scraper/radxa-flash
LOG=$BASE/flash.log
PS='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
: > "$LOG"

log(){ echo "[$(date +%H:%M:%S.%3N)] $*" | tee -a "$LOG"; }
ps_run(){ "$PS" -NoProfile -Command "$1" 2>&1 | tr -d '\r'; }

dmesg -C 2>/dev/null

# --- Phase 1 : attendre un cycle maskrom frais -------------------------------
log "Phase 1 : debranche le board, maintiens USB BOOT, rebranche."
log "          (attente de la disparition du GX-CHIP...)"
for i in $(seq 1 900); do
  ps_run "usbipd list" | grep -q "1b8e:c003" || { log "board absent, ok"; break; }
  sleep 0.5
done

log "Phase 2 : attente du GX-CHIP en maskrom..."
BUSID=""
for i in $(seq 1 900); do
  line=$(ps_run "usbipd list" | grep "1b8e:c003" | head -1)
  if [ -n "$line" ]; then
    BUSID=$(echo "$line" | awk '{print $1}')
    log "GX-CHIP detecte sur busid $BUSID"
    break
  fi
  sleep 0.5
done
[ -z "$BUSID" ] && { log "TIMEOUT : maskrom jamais detecte"; exit 1; }

# --- Phase 3 : attacher a WSL et booter --------------------------------------
sleep 0.5
log "Attachement de $BUSID a WSL..."
ps_run "usbipd attach --wsl --busid $BUSID" | while IFS= read -r l; do log "  $l"; done

for i in $(seq 1 40); do
  lsusb -d 1b8e:c003 >/dev/null 2>&1 && break
  sleep 0.25
done
lsusb -d 1b8e:c003 >/dev/null 2>&1 || { log "ERREUR : device non visible dans WSL"; exit 1; }

log "--- lancement boot-g12.py ---"
cd "$BASE/pyamlboot"
export AMLBOOT_TIMEOUT=${AMLBOOT_TIMEOUT:-15000}
export AMLBOOT_RETRIES=${AMLBOOT_RETRIES:-4}
export AMLBOOT_VERBOSE=${AMLBOOT_VERBOSE:-0}
timeout 180 python3 -u boot-g12.py "$BASE/rz-udisk-loader.bin" 2>&1 \
  | while IFS= read -r l; do echo "[$(date +%H:%M:%S.%3N)] $l" | tee -a "$LOG"; done
RC=${PIPESTATUS[0]}
log "--- boot-g12.py termine (code $RC) ---"

# --- Phase 4 : rendre la main a Windows IMMEDIATEMENT ------------------------
log "Detachement de $BUSID (la re-enumeration UMS doit rester cote Windows)"
ps_run "usbipd detach --busid $BUSID" | while IFS= read -r l; do log "  $l"; done

# --- Phase 5 : guetter l'apparition du disque UMS cote Windows ---------------
log "Attente du mode UMS (disque USB cote Windows), jusqu'a 90 s..."
for i in $(seq 1 90); do
  disks=$(ps_run "Get-Disk | Where-Object BusType -eq 'USB' | Select-Object -ExpandProperty Size")
  if [ -n "$disks" ]; then
    log ">>> DISQUE USB DETECTE apres ${i}s <<<"
    ps_run "Get-Disk | Where-Object BusType -eq 'USB' | Select-Object Number,FriendlyName,Size,PartitionStyle,OperationalStatus | Format-List" \
      | while IFS= read -r l; do log "  $l"; done
    ps_run "usbipd list" | while IFS= read -r l; do log "  $l"; done
    exit 0
  fi
  # trace de ce que voit usbipd pendant l'attente
  if [ $((i % 10)) -eq 0 ]; then
    log "  t=${i}s : $(ps_run "usbipd list" | grep -c '1b8e') device(s) 1b8e vu(s)"
  fi
  sleep 1
done

log "TIMEOUT : aucun disque USB apparu"
log "--- usbipd list ---"; ps_run "usbipd list" | while IFS= read -r l; do log "  $l"; done
log "--- entrees PnP 1b8e presentes ---"
ps_run "Get-PnpDevice -PresentOnly | Where-Object { \$_.InstanceId -like '*VID_1B8E*' } | Select-Object Status,Class,FriendlyName,InstanceId | Format-List" \
  | while IFS= read -r l; do log "  $l"; done
log "--- dmesg ---"; dmesg 2>&1 | tail -30 | tee -a "$LOG"
