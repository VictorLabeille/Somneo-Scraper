#!/usr/bin/env python3
import sys, time, usb.core

dev = usb.core.find(idVendor=0x1b8e, idProduct=0xc003)
if dev is None:
    print("DEVICE INTROUVABLE"); sys.exit(1)

print(">> reset() du port USB...")
try:
    dev.reset()
    print("   reset envoye")
except Exception as e:
    print("   reset -> %r" % e)

time.sleep(3)

dev = usb.core.find(idVendor=0x1b8e, idProduct=0xc003)
if dev is None:
    print("DEVICE DISPARU apres reset (detache de WSL ?)"); sys.exit(2)

print(">> re-test apres reset")
for label, args in (("GET_DESCRIPTOR std", (0x80, 0x06, 0x0100, 0, 18)),
                    ("VENDOR identify   ", (0xc0, 0x02, 0, 0, 8))):
    try:
        t0 = time.time()
        r = dev.ctrl_transfer(*args, timeout=4000)
        print("[OK ] %s (%.0f ms): %s" % (label, (time.time()-t0)*1000, bytes(r).hex()))
    except Exception as e:
        print("[KO ] %s -> %r" % (label, e))
