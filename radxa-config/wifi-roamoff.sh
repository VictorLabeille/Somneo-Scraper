#!/bin/sh
# Rend a wpa_supplicant la decision du point d'acces, puis REDEMARRE la carte.
#
# Usage (en root, SUR LA CARTE) :
#     scp radxa-config/wifi-roamoff.sh radxa:
#     ssh -t radxa 'sudo ~/wifi-roamoff.sh'
#
# ATTENTION : ce script se termine par `systemctl reboot`, sans confirmation. Le
# parametre n'est lu qu'au chargement du module, il n'y a pas d'autre moyen de
# l'appliquer qu'un redemarrage (ou un rechargement de brcmfmac, plus risque a
# distance). Deja applique le 23/08/2026 ; le relancer n'a d'interet qu'apres une
# reinstallation du systeme.
#
# Constat du 23/08/2026 : la carte s'est retrouvee en 5 GHz (canal 112, DFS,
# -75 dBm) alors que freq_list limite bien le reseau aux canaux 2,4 GHz -- et
# l'option est toujours active dans la configuration. Le journal montre pourquoi :
# les associations vers le BSS 5 GHz arrivent sans "Trying to associate", donc
# sans passer par wpa_supplicant. C'est le roaming interne du firmware du
# CYW43455, qui court-circuite freq_list.
#
# roamoff=1 desactive ce roaming firmware ; wpa_supplicant reprend la main et ses
# propres associations respectent freq_list (verifie : elles vont toujours sur le
# BSS 2,4 GHz).
#
# Le module n'est pas dans l'initramfs (verifie avec lsinitramfs) : ce fichier
# suffit, pas besoin de reconstruire l'initrd ni de toucher au chemin de boot.
set -eu

[ "$(id -u)" -eq 0 ] || { echo "A lancer en root."; exit 1; }

echo "== etat WiFi avant =="
/usr/sbin/iw dev wlan0 link | sed -n '1,6p' || true

echo
echo "== ecriture de /etc/modprobe.d/brcmfmac.conf =="
cat > /etc/modprobe.d/brcmfmac.conf <<'CONF'
# Le firmware du CYW43455 change de point d'acces de lui-meme, sans passer par
# wpa_supplicant : freq_list est alors sans effet et la carte part en 5 GHz.
# roamoff=1 rend la decision de roaming a wpa_supplicant.
options brcmfmac roamoff=1
CONF
chmod 644 /etc/modprobe.d/brcmfmac.conf
cat /etc/modprobe.d/brcmfmac.conf

echo
echo "== prise en compte par modprobe =="
modprobe --showconfig | grep -i "brcmfmac" || echo "ATTENTION : ligne non vue par modprobe"

echo
echo "== redemarrage dans 3 s (la connexion SSH va tomber) =="
sleep 3
systemctl reboot
