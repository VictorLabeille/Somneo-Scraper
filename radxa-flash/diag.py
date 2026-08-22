#!/usr/bin/env python3
"""Discrimine: transfert standard OK vs transfert vendor KO."""
import sys, time, usb.core, usb.util

dev = usb.core.find(idVendor=0x1b8e, idProduct=0xc003)
if dev is None:
    print("DEVICE INTROUVABLE (1b8e:c003)"); sys.exit(1)

print("Device trouve: bus=%d addr=%d speed=%s" % (dev.bus, dev.address, dev.speed))
print("bcdUSB=0x%04x bcdDevice=0x%04x" % (dev.bcdUSB, dev.bcdDevice))

# 1) Transfert de controle STANDARD (GET_DESCRIPTOR device)
try:
    t0 = time.time()
    d = dev.ctrl_transfer(0x80, 0x06, 0x0100, 0, 18, timeout=3000)
    print("[OK ] GET_DESCRIPTOR standard  (%d octets en %.0f ms): %s"
          % (len(d), (time.time()-t0)*1000, bytes(d).hex()))
except Exception as e:
    print("[KO ] GET_DESCRIPTOR standard -> %r" % e)

# 2) Lecture des strings (passe par des transferts de controle)
for idx, name in ((dev.iManufacturer, "iManufacturer"), (dev.iProduct, "iProduct")):
    try:
        t0 = time.time()
        s = usb.util.get_string(dev, idx)
        print("[OK ] %-14s (%.0f ms) = %r" % (name, (time.time()-t0)*1000, s))
    except Exception as e:
        print("[KO ] %-14s -> %r" % (name, e))

# 3) Requete VENDOR = REQ_IDENTIFY_HOST (celle qui echoue dans boot-g12.py)
for attempt in range(3):
    try:
        t0 = time.time()
        r = dev.ctrl_transfer(0xc0, 0x02, 0, 0, 8, timeout=5000)
        print("[OK ] VENDOR identify essai %d (%.0f ms): %s"
              % (attempt+1, (time.time()-t0)*1000, bytes(r).hex()))
        break
    except Exception as e:
        print("[KO ] VENDOR identify essai %d (%.0f ms) -> %r"
              % (attempt+1, (time.time()-t0)*1000, e))
        time.sleep(1)
