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

## Pièges du flash — `radxa-flash/`

Ces points ont coûté une session entière de débogage. Les relire avant de toucher au flash.
La procédure elle-même est dans le `README.md`.

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

## Le poste de dev ne peut pas découvrir le réveil

WSL2 est derrière un NAT et le multicast SSDP ne le franchit pas. Toute sonde vers le Somneo
doit partir de la Radxa, par SSH. Aucun réglage WSL n'y change rien : ne pas relancer cette
impasse.
