#!/bin/bash
try(){ echo "=== fastboot $*"; timeout 15 fastboot "$@" 2>&1 | head -3; echo; }
try oem help
try oem run ums 0 mmc 0
try oem ums
try oem format
try getvar partition-size:bootloader
try flashing get_unlock_ability
