#!/bin/bash
# Injecte une configuration WiFi dans une image Armbian AVANT son ecriture sur l'eMMC.
#
# Pourquoi : la Radxa Zero n'a ni Ethernet ni console serie, et le projet n'utilise pas de
# microSD. Le seul acces headless possible consiste donc a preconfigurer le reseau dans
# l'image elle-meme.
#
# On monte l'IMAGE en loop device, pas le disque physique : `wsl --mount` echoue sur le
# gadget UMS d'U-Boot, et `usb-storage` a travers USB/IP boucle sur des resets.
#
# Le mot de passe n'est jamais ecrit en clair : il est converti en cle derivee
# PBKDF2-HMAC-SHA1, seule forme stockee sur la carte.
#
# Usage (en root) :
#     IMG=/chemin/vers/image.img CRED=~/.wifi-radxa ./preconfig-wifi.sh
#
# Le fichier d'identifiants contient deux lignes :
#     SSID=NomDuReseau
#     PSK=MotDePasse
# Le creer en droits 600 et le supprimer apres usage.

set -e

IMG=${IMG:?definir IMG=/chemin/vers/image.img}
CRED=${CRED:-$HOME/.wifi-radxa}
MNT=${MNT:-/mnt/radxa}
COUNTRY=${COUNTRY:-FR}

# Canaux 1-13 de la bande 2,4 GHz (domaine reglementaire europeen). wpa_supplicant
# ignorera toute frequence absente de cette liste : la carte ne s'associera donc pas en
# 5 GHz, meme si le SSID y est aussi diffuse. Choix delibere — le trafic se limite a des
# requetes REST de quelques Ko vers le Somneo, la portee prime sur le debit.
# Laisser FREQ vide pour autoriser les deux bandes.
FREQ=${FREQ:-"2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462 2467 2472"}

[ -f "$IMG" ]  || { echo "Image introuvable : $IMG"; exit 1; }
[ -f "$CRED" ] || { echo "Identifiants introuvables : $CRED"; exit 1; }
[ "$(id -u)" -eq 0 ] || { echo "A lancer en root."; exit 1; }

SSID=$(sed -n 's/^SSID=//p' "$CRED")
[ -n "$SSID" ] || { echo "SSID vide dans $CRED"; exit 1; }
echo "SSID cible : $SSID"

PSK_HEX=$(CRED="$CRED" python3 - <<'PY'
import hashlib, os, io
d = dict(l.split("=", 1) for l in io.open(os.environ["CRED"], encoding="utf-8").read().splitlines() if "=" in l)
print(hashlib.pbkdf2_hmac("sha1", d["PSK"].encode(), d["SSID"].encode(), 4096, 32).hex())
PY
)
[ ${#PSK_HEX} -eq 64 ] || { echo "Cle derivee invalide."; exit 1; }
echo "Cle derivee : OK"

losetup -D 2>/dev/null || true
LOOP=$(losetup -f -P --show "$IMG")
mkdir -p "$MNT"
mount "${LOOP}p1" "$MNT"
trap 'umount "$MNT" 2>/dev/null; losetup -d "$LOOP" 2>/dev/null' EXIT

# Attention : NetworkManager n'est PAS installe sur l'image minimale (seuls deux fragments
# de conf y traînent, vestiges de la construction). Le trio actif est systemd-networkd +
# wpa_supplicant + ssh. Configurer NetworkManager ici serait sans effet.
[ -d "$MNT/usr/lib/systemd/system" ] || { echo "Ce n'est pas un rootfs."; exit 1; }

CONF="$MNT/etc/wpa_supplicant/wpa_supplicant-wlan0.conf"
install -d -m 755 "$MNT/etc/wpa_supplicant"
{
  echo "ctrl_interface=DIR=/run/wpa_supplicant GROUP=netdev"
  echo "update_config=1"
  echo "country=$COUNTRY"
  echo
  echo "network={"
  printf '\tssid="%s"\n' "$SSID"
  printf '\tpsk=%s\n' "$PSK_HEX"
  printf '\tkey_mgmt=WPA-PSK\n'
  [ -n "$FREQ" ] && printf '\tfreq_list=%s\n' "$FREQ"
  echo "}"
} > "$CONF"
chmod 600 "$CONF"
echo "ecrit : /etc/wpa_supplicant/wpa_supplicant-wlan0.conf (600)"

ln -sf /usr/lib/systemd/system/wpa_supplicant@.service \
       "$MNT/etc/systemd/system/multi-user.target.wants/wpa_supplicant@wlan0.service"
echo "active : wpa_supplicant@wlan0.service"

install -d -m 755 "$MNT/etc/systemd/network"
# Name=wl* plutot que wlan0 : couvre une eventuelle variation du nom d'interface.
printf '[Match]\nName=wl*\n\n[Network]\nDHCP=yes\n\n[DHCPv4]\nRouteMetric=20\n' \
  > "$MNT/etc/systemd/network/25-wlan.network"
echo "ecrit : /etc/systemd/network/25-wlan.network"

echo
echo "=== relecture (cle masquee) ==="
sed 's/^\tpsk=.*/\tpsk=<masque>/' "$CONF"
sync
echo
echo "Termine. L'image peut etre ecrite sur l'eMMC."
