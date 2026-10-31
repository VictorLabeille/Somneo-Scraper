# La Radxa Zero — état en service, pièges et diagnostics

> Référence technique de la carte qui héberge le collecteur. Déplacé depuis `AGENTS.md` le
> 5 septembre 2026 : ce sont des faits et des diagnostics sur un appareil, pas de
> l'aiguillage — leur place est dans `docs/`.
>
> `AGENTS.md` ne garde que les règles impératives qui en découlent et renvoie ici.

## État du matériel — ce qui est acquis

- La Radxa Zero tourne sous **Armbian 26.2.1 (Trixie)** installé sur son eMMC. 1,89 Go de RAM
  utilisable, rootfs de 6,9 Go. Noyau `6.18.43-current-meson64` au 23 août 2026 — il a été
  mis à jour depuis l'installation, ne pas s'étonner de le voir bouger.
- Elle est **alimentée par le port USB du Somneo** et fixée à l'arrière du réveil depuis le
  22 août 2026, en fonctionnement continu. Conséquence à peser avant toute manipulation :
  y accéder physiquement suppose de défaire l'installation, et la carte n'a ni console série
  ni écran. Préférer systématiquement ce qui est réversible à chaud à ce qui touche au
  chemin de boot.
- Elle est autonome sur le réseau domestique en **WiFi 2,4 GHz**, hostname `radxa-zero`,
  adresse attribuée par DHCP (non figée — 192.168.1.198 au 23 août 2026, à ne jamais coder
  en dur).
- Accès **SSH par clé uniquement**. Depuis le poste de dev : `ssh radxa`.
- Sa LED verte est éteinte de 22 h à 8 h.

## Outillage Python — ce que l'image minimale ne fournit pas

Armbian minimal livre **Python 3.13.5 et rien d'autre** : ni `pip`, ni `venv`, ni `requests`
(constaté le 2026-09-06). Ce n'est pas un interdit, c'est un point de départ — installer ce
qu'il faut par `apt` est sans risque, cela ne touche ni le chemin de boot ni le réseau.

Deux usages, deux traitements :

- **Les sondes de diagnostic** tiennent en **bibliothèque standard** — `urllib.request` avec un
  contexte SSL non vérifié (le Somneo a un certificat auto-signé) et `socket` pour le M-SEARCH
  SSDP. Elles restent ainsi copiables et exécutables telles quelles, sans rien préparer.
- **Le collecteur et les essais de bibliothèque** vivent dans un **venv**, Debian Trixie
  refusant les installations dans le Python système (PEP 668). C'est là que `pysomneo` est
  installé en mode éditable pour être éprouvé contre l'appareil réel.

## Accès au board

Hôte `radxa-zero`, joignable en SSH sur le WiFi domestique (bail DHCP, IP non figée). Le WiFi
est configuré dans l'image avant écriture, via
`/etc/wpa_supplicant/wpa_supplicant-wlan0.conf` — NetworkManager **n'est pas installé** sur
l'image minimale, ne pas perdre de temps à le configurer. La bande est restreinte au
2,4 GHz par `freq_list` : portée et stabilité priment sur le débit, le trafic se limitant à
des requêtes REST de quelques Ko vers le Somneo.

### Le roaming du firmware WiFi — piège vérifié le 23 août 2026

**`freq_list` ne suffit pas.** La carte s'est retrouvée associée au BSS 5 GHz de la box
(canal 112, DFS, -75 dBm) alors que l'option était toujours présente et active dans la
configuration. En cause : le **roaming interne du firmware** du CYW43455 (`brcmfmac`), qui
change de point d'accès de lui-même sans passer par wpa_supplicant — donc sans que
`freq_list` ait son mot à dire. La signature est nette dans
`journalctl -u wpa_supplicant@wlan0` : les associations subies arrivent **sans** la ligne
« Trying to associate » qui précède toujours celles que wpa_supplicant décide.

```
Trying to associate with 62:… (SSID='…' freq=2437 MHz)   <- wpa_supplicant : 2,4 GHz
Associated with 62:…
Associated with 72:…                                      <- firmware : 5 GHz, sans préavis
```

La parade est `options brcmfmac roamoff=1` dans `/etc/modprobe.d/brcmfmac.conf`, posé par
`radxa-config/wifi-roamoff.sh` et effectif depuis le redémarrage du 23 août 2026 : retour au
canal 6 à **-57 dBm** au lieu de -75, et 72 Mbit/s au lieu de 27. Le module n'étant pas
embarqué dans l'initramfs (`lsinitramfs` le confirme), aucun `update-initramfs` n'est
nécessaire — rien à toucher dans le chemin de boot.

Si le symptôme réapparaît, chercher d'abord une association « nue » dans le journal avant de
suspecter `wpa_supplicant-wlan0.conf` : cette configuration-là, elle, est correcte.

### Lire une déconnexion dans le journal — ce que `brcmfmac` écrit vraiment

Vérifié le 2026-10-25 dans le code source du noyau 6.18
(`drivers/net/wireless/broadcom/brcm80211/brcmfmac/cfg80211.c`). Avec ce pilote, c'est le
firmware qui gère l'association ; wpa_supplicant ne voit que ce que le pilote lui remonte, et
deux des lignes du journal ne disent pas ce qu'elles ont l'air de dire.

- **`CTRL-EVENT-DISCONNECTED reason=0 locally_generated=1`** : ce n'est **pas** la carte qui a
  choisi de partir. `brcmf_map_fw_linkdown_reason()` met `reason=0` pour tout événement autre
  qu'un deauth ou un disassoc, et `locally_generated` vaut 1 seulement pour l'événement
  `BRCMF_E_LINK` (lien perdu). C'est le firmware qui ne capte plus le point d'accès, en général
  par des balises manquées. Un deauth envoyé par la box arriverait avec `locally_generated=0`
  et son propre code de raison.
- **`CTRL-EVENT-ASSOC-REJECT status_code=16`** : ce n'est **pas** forcément la box qui refuse.
  Dans la norme, 16 veut dire `WLAN_STATUS_AUTH_TIMEOUT` (authentification abandonnée faute de
  la trame suivante dans le délai). Mais `brcmf_bss_connect_done()` met **16 en dur pour tout
  échec de connexion**, quelle qu'en soit la cause : `SET_SSID` en échec, réseau introuvable
  (`BRCMF_E_STATUS_NO_NETWORKS`), échec du supplicant interne. Le statut réel de la box, s'il
  y en a un, n'est pas transmis.

Lecture d'un incident : décrochage `locally_generated=1` puis une série de `status_code=16`, cela
signifie « le firmware a perdu la box, puis ne l'a pas retrouvée pendant N minutes ». Ce que ce
couple ne dit pas, c'est de quel côté vient la panne (radio de la carte, signal, box).

### Diagnostiquer les décrochages — ce qui se mesure sans root, et ce qui manque

Relevé du 2026-10-25, en lecture seule.

- **`iw dev wlan0 get power_save` répond `off`** sans aucun réglage de notre part : l'économie
  d'énergie du WiFi n'est pas active sur cette image.
- **L'historique d'`apt` et de `dpkg` est perdu.** Armbian tient `/var/log` en RAM (zram,
  `armbian-ramlog`) et le recopie dans `/var/log.hdd` ; `history.log` et `dpkg.log` y sont vides
  depuis la rotation du 1er septembre, et le `.1.gz` restant date de la fabrication de l'image.
  Pour dater une mise à jour, lire les dates des fichiers de dpkg, réécrits à chaque
  installation : `ls -lt --time-style=+%F_%T /var/lib/dpkg/info/*.list | head`. Au
  2026-10-25, le plus récent date de l'installation du 2026-08-22 : noyau, `armbian-firmware`
  et `wpasupplicant` n'ont pas bougé depuis.
- **Le journal ne remonte qu'à un mois** (`SystemMaxUse=20M`) : les messages du noyau au
  démarrage disparaissent avec lui. Le boot du 2026-10-28 nomme le firmware chargé :
  `BCM4345/6 wl0: Aug 25 2015 18:58:57 version 7.45.69 (r581703)`. En marche, le noyau n'écrit
  rien lors d'un décrochage : ni erreur SDIO, ni plantage du firmware.
- **Une coupure de courant perd le journal depuis la dernière recopie** : `/var/log` est en RAM,
  et le journal du boot précédent s'arrête alors à la dernière recopie d'`armbian-ramlog` (22 h
  plus tôt le 2026-10-28, coupure vérifiée). Le collecteur écrit sur l'eMMC : `heartbeat` et
  `outage` restent la source pour dater ce genre d'arrêt.
- **`iw dev wlan0 survey dump` ne renvoie rien** avec ce firmware : pas de mesure d'occupation
  du canal par la carte.
- **`ping` exige root** (`cap_net_raw` absent) ; `iw dev wlan0 station dump` (signal, `tx
  failed`, débit, durée de connexion) se lit sans root, comme `journalctl -u
  wpa_supplicant@wlan0`. Un balayage des réseaux voisins (`iw dev wlan0 scan`) demande root.
- **`iw dev wlan0 scan dump` se lit sans root** : il rend le cache des derniers scans, y compris
  ceux que wpa_supplicant lance pendant une panne. C'est la façon de savoir, hors connexion, si
  la carte entend encore la box et les voisins.
- **`freq_list` ne limite que le choix du réseau, pas les balayages.** Un `sudo iw dev wlan0
  scan` couvre aussi le 5 GHz, et ceux de wpa_supplicant aussi : pendant les pannes du
  2026-10-25 au 2026-10-27, la box en 5 GHz est reçue dans 96 % des balayages (718). `scan_freq`
  serait l'option qui limite ceux de wpa_supplicant (non essayée). Pour un scan à la main limité
  au 2,4 GHz :
  `iw dev wlan0 scan freq 2412 2417 2422 2427 2432 2437 2442 2447 2452 2457 2462 2467 2472`.
- **wpa_supplicant n'annonce pas le 5 GHz à la box** quand `freq_list` est posé dans le bloc
  `network` : l'élément « Supported Operating Classes » de la demande d'association omet alors
  les classes 115 à 130 (`wpa_supplicant/op_classes.c`, `wpas_op_class_supported()`, version
  2.10). Posé en global, `freq_list` n'a pas cet effet.
- **wpa_supplicant allonge lui-même les pannes.** Après chaque échec de connexion,
  `wpas_auth_failed()` désactive le réseau 10, 20, 30, 60, 60 puis 90 s, plus un aléa
  (`CTRL-EVENT-SSID-TEMP-DISABLED … duration=`). Ces durées sont en dur dans la version 2.10 et
  aucune option ne les règle ; `wpa_cli -i wlan0 enable_network 0` lève l'attente en cours.
  La liste d'ignorés (`BSSID … ignore list count incremented`) ne bloque pas un point d'accès
  unique, elle change seulement l'ordre des essais.
- **Une tentative d'association ne prouve pas que la box est entendue** : wpa_supplicant tente
  aussi sur un résultat de scan en cache. Mesuré sur 48 h, 718 balayages en panne : la box en
  2,4 GHz est reçue dans 12 % d'entre eux, à son niveau normal, alors que la box en 5 GHz et les
  voisins restent reçus, et 2 balayages seulement ne reçoivent rien. `iw dev wlan0 scan dump` donne l'âge
  de chaque réception (`last seen … ms ago`) : le filtrer, le cache garde les réseaux absents.
  L'enregistreur ne lit ce cache que déconnecté : ces taux n'ont pas de référence hors panne.
- **Les pannes se comptent dans le journal de wpa_supplicant** : une panne va d'un
  `CTRL-EVENT-DISCONNECTED` au `CTRL-EVENT-CONNECTED` suivant, avec les `ASSOC-REJECT` entre
  les deux. Comparer les instants en secondes (`journalctl -o short-unix`). La table `outage` du
  collecteur en voit moins : une panne plus courte que l'intervalle de collecte lui échappe.

Le mot de passe WiFi n'est jamais stocké en clair : il est converti en clé dérivée
PBKDF2-HMAC-SHA1 avant d'être écrit dans l'image.

### SSH durci — et pourquoi un fichier séparé

Authentification par clé ed25519 uniquement, mot de passe et login root désactivés via
`/etc/ssh/sshd_config.d/99-durcissement.conf`. Depuis le poste de dev, raccourci `ssh radxa`
(entrée dans `~/.ssh/config`).

Pourquoi un fichier séparé plutôt qu'une édition de `sshd_config` : contrairement à la
plupart des fichiers de configuration, **`sshd` retient la première valeur rencontrée pour
chaque option, pas la dernière**. Or `sshd_config` contient `PermitRootLogin yes` en ligne 33
et charge `/etc/ssh/sshd_config.d/*.conf` dès la ligne 12. Le fichier de surcharge est donc lu
*avant*, et c'est lui qui gagne — sans qu'on ait à toucher au fichier d'origine :

```
ligne 12 : Include /etc/ssh/sshd_config.d/*.conf   -> PermitRootLogin no   ← retenu
ligne 33 : PermitRootLogin yes                     -> ignore (deja defini)
```

Si l'`Include` se trouvait *après* la ligne 33, la surcharge n'aurait aucun effet. Vérifier
sa position avant de compter dessus.

**Toujours valider par `sshd -t` avant un `systemctl reload ssh`** : sans cela, une erreur de
syntaxe coupe définitivement l'accès à une carte qui n'a ni console série ni écran.

## LED d'alimentation — extinction nocturne

La carte étant dans une chambre, sa LED verte est éteinte de 22 h à 8 h par
`radxa-config/led-schedule.sh`, à lancer en root **sur la carte** (il est idempotent).

Trois points à connaître avant d'y toucher :

- **Rien ne déclare la LED dans le device tree** : `/sys/class/leds` est vide, seul libgpiod
  peut la piloter. Armbian fournit bien des overlays (`meson-g12a-radxa-zero-gpio-10-led`),
  mais les activer suppose de modifier `armbianEnv.txt`, donc le chemin de boot — écarté
  pour un simple confort, sur une carte qu'on ne peut pas dépanner sans démonter le réveil.
- Elle est sur la **ligne 10 du domaine GPIO AO** (`gpiochip1`, nœud
  `bus@ff800000/pinctrl@14`). Sur les révisions v1.51+, la broche 38 n'est pas reliée au
  header 40 points : elle reste donc `unnamed` dans `gpioinfo`, à l'inverse des autres,
  nommées d'après le header (« 35 [GPIOAO_8] »). C'est pourquoi `gpioset GPIOAO_10=0` échoue
  avec « cannot find line » et qu'il faut passer par l'offset. Le numéro de `gpiochip`
  n'étant pas garanti stable d'un démarrage à l'autre, le script le résout par son nœud de
  device tree plutôt que de figer `gpiochip1`.
- **Relâcher la ligne ne rallume pas la LED** : la broche repasse en entrée haute impédance
  et l'état « allumé » posé au démarrage par le bootloader est perdu. Il faut donc tenir la
  ligne à 1 le jour *et* à 0 la nuit — d'où deux services qui se relaient par `Conflicts=`,
  plutôt qu'un service unique qu'on arrêterait le soir.

`radxa-led-boot.service` repose l'état correct au démarrage : les minuteries systemd ne
rattrapent pas un déclenchement manqué, un redémarrage à 2 h du matin laisserait sinon la
LED allumée jusqu'à 22 h le lendemain.

`radxa-config/led-probe.sh` sert à retrouver la bonne ligne si le besoin se représente : il
force successivement les lignes 10 puis 8 et laisse l'œil trancher. La ligne qui ne pilote
pas la LED part vers le header 40 points, où rien n'est branché — les deux essais sont donc
sans conséquence.

## Heure au démarrage — aucune horloge ne survit à une coupure

Relevé le 2026-09-15, en lecture seule. La carte est alimentée par le réveil : **une coupure de
courant est son arrêt le plus probable**, et rien ne lui garde l'heure pendant ce temps.

- **RTC virtuel** : `/sys/class/rtc/rtc0/name` vaut `meson-vrtc`, `hctosys` vaut 0. Rien n'est
  gardé hors tension.
- **`fake-hwclock` est installé mais masqué.** C'est `systemd-timesyncd` (systemd 257) qui tient
  l'heure d'un démarrage à l'autre. Il enregistre l'horloge régulièrement
  (`/var/lib/systemd/timesync/clock`). Au démarrage, il **ramène l'horloge à cet instant**
  (« System clock time advanced to recorded timestamp »), puis la synchronise quand le réseau
  vient : **à 51 s** au démarrage du 2026-08-23. C'était un simple redémarrage, et l'instant
  enregistré retardait d'une vingtaine de secondes. Après une coupure de durée D, tout ce qui
  démarre avant la synchronisation voit une heure en retard d'environ D.
- **`time-sync.target` ne garantit rien** : `systemd-time-wait-sync` est désactivé, et s'il
  était activé il attendrait sans limite (`TimeoutStartUSec=infinity`). Sans Internet, tout ce
  qui en dépend ne démarrerait jamais, même avec le réveil joignable.
- **`/run/systemd/timesync/synchronized`** est posé par `timesyncd` à la synchronisation et
  effacé à chaque démarrage (`/run` est en mémoire). C'est lui que le collecteur attend avant
  de dater quoi que ce soit (écart 5 de `.claude/specs/2026-09-14-ecarts-contrat-sleepmaxxer.md`).

## Pièges du flash — `radxa-flash/`

Ces points ont coûté une session entière de débogage. Les relire avant de toucher au flash.
L'inventaire de l'outillage et la procédure suivent.

- **`pyamlboot/` est modifié localement.** Les timeouts USB codés en dur (1000 ms, et 100 ms
  sur une lecture) sont trop courts via USB/IP, où chaque URB ajoute un aller-retour TCP.
  Ils sont pilotés par `AMLBOOT_TIMEOUT` (défaut 15 s). L'original est dans
  `pyamlboot.py.orig`. Ne pas écraser par un `git pull` du dépôt amont sans reporter le patch.
- **La phase bootrom ne fonctionne que depuis Linux.** En natif Windows, la pile USB ne sert
  jamais l'endpoint bulk IN après `run()` : `getBootAMLC()` expire systématiquement. Passer
  par WSL2 + `usbipd`.
- **La fenêtre maskrom expire en 1 à 2 minutes** tout en laissant le périphérique énuméré et
  `Status: OK` côté Windows. Enchaîner sans attendre — d'où l'automatisation de `go.sh`.
- **Ne jamais rattacher le gadget UMS à WSL** via `usbipd` : `usb-storage` boucle sur des
  resets, ne crée aucun `/dev/sd*`, et fait tomber le gadget en `CM_PROB_FAILED_START`.
  L'écriture de l'image se fait côté Windows.
- Si U-Boot ne voit plus l'eMMC (`ums 0 mmc 0` échoue, le board disparaît du bus),
  passer `radxa-zero-erase-emmc.bin` avant de réessayer le loader udisk.
- **Le gadget UMS sature au-delà d'environ 167 Mio d'écriture continue** : il retire le
  média en pleine opération et Windows renvoie une erreur d'E/S. Le piège est que l'échec
  se reproduit au même octet et résiste aux reprises, ce qui **imite un secteur défectueux**.
  Vérifier `Get-Disk` juste après une erreur : `No Media` désigne le gadget, pas la mémoire.
  La parade est une **pause de 150 ms toutes les 16 Mo** (`-ThrottleMs` / `-ThrottleEveryMB`),
  et surtout pas de `Flush()` par bloc — les commandes de synchronisation de cache achèvent
  le gadget.
- Le loader échoue environ une fois sur deux au démarrage (timeout bootrom via USB/IP).
  Refaire un cycle, ce n'est pas symptomatique d'une panne.

## Outillage de `radxa-flash/` — inventaire

Scripts issus de la session de débogage où l'outil officiel Windows (RZ USB Boot Helper) restait
bloqué à « Start run » et où l'eMMC s'est révélée corrompue. **Terminé et archivé** : ne pas
modifier sauf réinstallation explicite du système. Le détail de l'enquête est dans la note
Obsidian `Projets/Somneo-Scraper.md`.

**Orchestration (WSL2)** — la phase bootrom ne fonctionne que depuis Linux :

| Fichier | Rôle |
| --- | --- |
| `go.sh` | Script principal. Attache le board à WSL via `usbipd`, charge un loader avec `boot-g12.py`, **détache immédiatement** après `[BL2 END]`, puis observe l'apparition du disque. Loader réglable par `LOADER=…`. |
| `run_and_watch.sh` | Variante qui attend d'abord un cycle maskrom frais (débranchement puis réapparition) avant de démarrer. |
| `autoflash.sh`, `boot_to_ums.sh` | Versions antérieures, conservées pour référence. |
| `preconfig-wifi.sh` | Injecte la configuration WiFi dans l'image **avant** écriture (montage en loop device, clé dérivée PBKDF2, activation de `wpa_supplicant@wlan0`). Lit les identifiants depuis un fichier externe, jamais versionné. |

**Diagnostic USB** :

| Fichier | Rôle |
| --- | --- |
| `diag.py` | Compare transferts de contrôle standard et requêtes vendor, pour distinguer un lien USB mort d'un bootrom qui ne répond plus. |
| `reset_test.py` | Reset de port USB et re-test. |
| `ubcmd.py` | Envoi de commandes U-Boot arbitraires via `bulkCmd` (inopérant sur le gadget fastboot, conservé pour d'autres loaders). |
| `oemtest.sh` | Sonde les commandes `fastboot oem` et les variables de partition. |

**Loaders Radxa** (téléchargés depuis `dl.radxa.com`, non versionnés) :

| Fichier | Rôle |
| --- | --- |
| `rz-udisk-loader.bin` | Expose l'eMMC en USB Mass Storage — le loader à utiliser pour flasher. |
| `radxa-zero-erase-emmc.bin` | Efface l'eMMC. Indispensable si U-Boot n'arrive plus à l'initialiser. |
| `rz-fastboot-loader.bin` | Expose un gadget fastboot. Surtout utile en diagnostic : il démarre même quand l'eMMC est illisible, ce qui permet d'isoler la panne. |

**Scripts côté Windows** — l'écriture de l'image se fait depuis Windows, pas depuis WSL
(`usb-storage` boucle sur des resets à travers USB/IP et ne crée jamais de `/dev/sd*`). Ils vivent
hors du dépôt, dans `C:\Users\Victor\radxa-flash\`, **à consolider dans le dépôt lors d'une
prochaine passe** :

| Fichier | Rôle |
| --- | --- |
| `flash_emmc.ps1` | Écrit l'image sur l'eMMC. Refuse toute cible qui ne soit pas un disque USB nommé `*UMS disk*`, de 4 à 16 Go, différent du disque 0. Écrit par blocs de 4 Mo avec une **pause de 150 ms tous les 16 Mo** (voir pièges), consigne sa progression dans `flash_progress.txt` et accepte `-StartOffset` pour reprendre après interruption. |
| `verify_emmc.ps1` | Relit l'eMMC et compare les empreintes SHA256 avec l'image (`-Full` pour l'intégralité). |
| `check_ext4.ps1` | Lit le superbloc ext4 et rapporte le compteur de montages — preuve directe que le board a démarré. |
| `boot_win.py` | Portage du boot bootrom en Python natif Windows. **Ne fonctionne pas** : la pile USB de Windows ne sert jamais l'endpoint bulk IN après `run()`. Conservé comme trace du diagnostic. |

**Dépendance** : `pyamlboot/` est un clone de superna9999/pyamlboot, modifié localement — voir les
pièges ci-dessus.

## Procédure de flash

Prérequis : WSL2 avec `usbipd-win` côté Windows, `usbutils`/`python3-usb` côté Debian.

1. Armer le guetteur : `bash radxa-flash/run_and_watch.sh` (il attend un cycle maskrom frais).
2. Mettre le board en maskrom : maintenir **USB BOOT**, brancher, relâcher à l'allumage de la LED.
3. Si le busid n'est pas encore réservé, en PowerShell **administrateur** :
   `usbipd bind --force --busid 2-1` (persistant, une seule fois).
4. Le script attache, charge le loader et détache aussitôt. L'eMMC apparaît sous Windows
   comme `Linux UMS disk 0`.
5. Lancer `flash_emmc.ps1` **sans attendre**, puis débrancher et rebrancher **sans** toucher
   au bouton : le board démarre sur Armbian.

Le loader échoue environ une fois sur deux au démarrage (timeout bootrom aléatoire via
USB/IP) : il suffit de refaire un cycle, ce n'est pas symptomatique.

### Préconfigurer le WiFi avant écriture

Sans Ethernet, sans console série et sans microSD, le seul accès headless passe par une
préconfiguration dans l'image. `wsl --mount` ne fonctionne pas sur ce gadget UMS : monter
l'**image** en loop device, pas le disque physique.

Attention : **NetworkManager n'est pas installé** sur l'image minimale (seuls des fragments
de conf y traînent). Le trio actif est `systemd-networkd` + `wpa_supplicant` + `ssh`. La
configuration se fait donc dans `/etc/wpa_supplicant/wpa_supplicant-wlan0.conf` (droits 600,
clé dérivée PBKDF2 plutôt que mot de passe en clair) avec activation de
`wpa_supplicant@wlan0.service`. `freq_list` restreint la carte aux canaux 2,4 GHz.

Attention : `freq_list` ne lie que les associations décidées par wpa_supplicant. Le firmware
du CYW43455 fait du roaming pour son propre compte et peut basculer la carte en 5 GHz sans
préavis ; il faut `options brcmfmac roamoff=1` pour l'en empêcher — voir le roaming du firmware
WiFi plus haut.

## Scripts de `radxa-config/` — configuration de la carte en service

**Déjà déployés.** Modifier un script sans le redéployer fait diverger le dépôt et la carte,
silencieusement.

| Fichier | Rôle |
| --- | --- |
| `led-schedule.sh` | Éteint la LED verte de la carte de 22 h à 8 h — elle est fixée au dos du réveil, dans une chambre. Installe deux services `gpioset` qui se relaient et leurs minuteries systemd. À lancer en root sur la carte ; idempotent. Le pourquoi (ligne 10 du GPIO AO, relâcher la ligne ne rallume pas la LED) est plus haut. |
| `wifi-roamoff.sh` | Pose `options brcmfmac roamoff=1`. **Se termine par un redémarrage** : le paramètre n'est lu qu'au chargement du module. Déjà appliqué ; utile après une réinstallation. |
| `led-probe.sh` | Diagnostic : force successivement les lignes 10 puis 8 du GPIO AO pour identifier celle qui pilote la LED (elle varie selon la révision de carte). À lancer en vue de la LED. |

## Le poste de dev ne peut pas découvrir le réveil

WSL2 est derrière un NAT et le multicast SSDP ne le franchit pas. Toute sonde vers le Somneo
doit partir de la Radxa, par SSH. Aucun réglage WSL n'y change rien : ne pas relancer cette
impasse.
