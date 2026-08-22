#!/usr/bin/env python3
"""Envoie des commandes U-Boot au gadget Amlogic (1b8e:2200) via bulkCmd."""
import sys
import time

sys.path.insert(0, "/home/victor/_PROJETS/Somneo-Scraper/radxa-flash/pyamlboot")
from pyamlboot import pyamlboot

dev = pyamlboot.AmlogicSoC(idVendor=0x1b8e, idProduct=0x2200, timeout=10)


def run(cmd, read_status=True, timeout=8000):
    print("=" * 62)
    print("U-Boot> %s" % cmd, flush=True)
    try:
        r = dev.bulkCmd(cmd, read_status=read_status, timeout=timeout)
        if r is None:
            print("  (pas de reponse lue)")
            return None
        try:
            txt = r.tobytes().decode("utf-8", "replace")
        except AttributeError:
            txt = bytes(r).decode("utf-8", "replace")
        print("  %s" % txt.strip().replace("\n", "\n  "))
        return txt
    except Exception as e:
        print("  ERREUR : %r" % e)
        return None


if len(sys.argv) > 1:
    run(" ".join(sys.argv[1:]))
    sys.exit(0)

# Diagnostic eMMC
for c in ["echo test-bulkcmd",
          "mmc list",
          "mmc dev 0",
          "mmc info",
          "mmc part",
          "mmc rescan"]:
    run(c)
    time.sleep(0.2)
