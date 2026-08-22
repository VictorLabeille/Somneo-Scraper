#!/bin/bash
# Guette l'apparition du GX-CHIP en maskrom et lance boot-g12.py IMMEDIATEMENT.
BASE=/home/victor/_PROJETS/Somneo-Scraper/radxa-flash
LOG=$BASE/flash.log
: > "$LOG"

log(){ echo "[$(date +%H:%M:%S.%3N)] $*" | tee -a "$LOG"; }

dmesg -C 2>/dev/null

# Phase 1: attendre que le board soit DEBRANCHE (garantit qu'on attrape un cycle frais)
log "Phase 1: en attente du DEBRANCHEMENT du board..."
for i in $(seq 1 900); do
  lsusb -d 1b8e:c003 >/dev/null 2>&1 || { log "Board absent, ok"; break; }
  sleep 0.2
done

# Phase 2: guetter la reapparition en maskrom
log "Phase 2: en attente du GX-CHIP 1b8e:c003 (maskrom)..."

for i in $(seq 1 900); do
  if lsusb -d 1b8e:c003 >/dev/null 2>&1; then
    log "DETECTE apres ${i} x 0.2s"
    break
  fi
  sleep 0.2
done

if ! lsusb -d 1b8e:c003 >/dev/null 2>&1; then
  log "TIMEOUT: jamais detecte"; exit 1
fi

sleep 0.4
log "--- lancement boot-g12.py (instrumente) ---"
cd "$BASE/pyamlboot"
export AMLBOOT_TIMEOUT=${AMLBOOT_TIMEOUT:-15000}
export AMLBOOT_RETRIES=${AMLBOOT_RETRIES:-4}
export AMLBOOT_VERBOSE=1
log "AMLBOOT_TIMEOUT=$AMLBOOT_TIMEOUT AMLBOOT_RETRIES=$AMLBOOT_RETRIES"
timeout 180 python3 -u boot-g12.py "$BASE/rz-udisk-loader.bin" 2>&1 \
  | while IFS= read -r l; do echo "[$(date +%H:%M:%S.%3N)] $l" | tee -a "$LOG"; done

# Signature discriminante : le board a-t-il DISPARU du bus (brownout) ou est-il
# toujours la mais mute (incident protocole) ?
log "--- presence immediate du board apres echec ---"
if lsusb -d 1b8e:c003 >/dev/null 2>&1; then
  log "board TOUJOURS PRESENT sur le bus (1b8e:c003) -> pas de coupure d'alim"
elif lsusb -d 1b8e:2200 >/dev/null 2>&1; then
  log "board passe en UMS (1b8e:2200) -> SUCCES"
else
  log "board ABSENT du bus -> deconnexion franche (piste alimentation/brownout)"
fi

log "--- boot-g12.py termine ---"

# Le loader met quelques secondes a exposer la eMMC en USB Mass Storage (1b8e:2200)
log "Attente du passage en mode UMS (1b8e:2200)..."
for i in $(seq 1 60); do
  if lsusb -d 1b8e:2200 >/dev/null 2>&1; then
    log ">>> MODE UMS DETECTE apres $((i/2))s <<<"; break
  fi
  sleep 0.5
done

log "--- lsusb apres ---"; lsusb 2>&1 | tee -a "$LOG"
log "--- disques ---"; lsblk -o NAME,SIZE,TYPE,MODEL 2>&1 | tee -a "$LOG"
log "--- dmesg ---"; dmesg 2>&1 | tail -40 | tee -a "$LOG"
