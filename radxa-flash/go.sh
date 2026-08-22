#!/bin/bash
# Attach + boot + detach immediat + observation. A lancer board DEJA en maskrom.
BASE=/home/victor/_PROJETS/Somneo-Scraper/radxa-flash
LOG=$BASE/go.log
PS='/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe'
: > "$LOG"
log(){ echo "[$(date +%H:%M:%S.%3N)] $*" | tee -a "$LOG"; }
ps_run(){ "$PS" -NoProfile -Command "$1" 2>&1 | tr -d '\r'; }

dmesg -C 2>/dev/null
log "attach 2-1 -> WSL"
ps_run "usbipd attach --wsl --busid 2-1" | while IFS= read -r l; do log "  $l"; done
for i in $(seq 1 50); do lsusb -d 1b8e:c003 >/dev/null 2>&1 && break; sleep 0.2; done
lsusb -d 1b8e:c003 >/dev/null 2>&1 || { log "ERREUR: pas visible dans WSL"; exit 1; }
log "visible dans WSL, lancement boot-g12.py"

cd "$BASE/pyamlboot"
export AMLBOOT_TIMEOUT=15000 AMLBOOT_RETRIES=4 AMLBOOT_VERBOSE=0
timeout 150 python3 -u boot-g12.py "$BASE/${LOADER:-rz-udisk-loader.bin}" 2>&1 \
  | grep -vE "Deprecation|import pkg" \
  | while IFS= read -r l; do echo "[$(date +%H:%M:%S.%3N)] $l" | tee -a "$LOG"; done

log "detach immediat"
ps_run "usbipd detach --busid 2-1" >/dev/null

log "=== observation 150 s (WSL + Windows) ==="
pw=""; pl=""
for i in $(seq 1 150); do
  w=$(ps_run "usbipd list" | grep -E "^[0-9]+-[0-9]+" | grep -viE "logitech|camera|bluetooth" | awk '{print $1":"$2}' | tr '\n' ' ')
  l=$(lsusb 2>/dev/null | grep -v "1d6b:000" | awk '{print $6}' | tr '\n' ' ')
  d=$(ps_run "Get-Disk | Where-Object BusType -eq 'USB' | Select-Object -ExpandProperty FriendlyName" | tr '\n' ' ')
  [ "$w" != "$pw" ] && { log "  t=${i}s WINDOWS 2-1 -> ${w:-<absent>}"; pw="$w"; }
  [ "$l" != "$pl" ] && { log "  t=${i}s WSL -> ${l:-<vide>}"; pl="$l"; }
  [ -n "$d" ] && { log "  t=${i}s >>> DISQUE USB : $d <<<"; break; }
  sleep 1
done
log "=== dmesg ==="; dmesg 2>&1 | tail -25 | tee -a "$LOG"
