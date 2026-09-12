# Somneo-Scraper

Serveur local qui interroge en continu un **réveil Philips Somneo** (HF3671/01) via son
API REST locale, afin de pouvoir **couper son accès internet** tout en conservant — et en
dépassant — les fonctions de l'application constructeur SleepMapper.

C'est le composant back-end du projet **SleepMaxxer** (l'application mobile qui remplace
SleepMapper). Somneo-Scraper collecte et historise les données ; SleepMaxxer les consomme.

## Pourquoi

Le Somneo est en permanence connecté aux serveurs Philips, et SleepMapper est lent au
démarrage. Or le réveil expose une **API REST locale** (HTTPS sur le réseau domestique,
découverte par SSDP) totalement indépendante du cloud. On peut donc l'isoler d'internet
et le piloter soi-même — vérifié le 31 août 2026 : cette API ne demande **aucune
authentification**, ni en lecture ni en écriture.

Mais elle **n'a aucune mémoire**. La rétro-ingénierie de SleepMapper a montré que ses
graphiques d'historique viennent du cloud Philips, alimenté par le réveil lui-même
(échantillon toutes les 15 min, port `dataupload`) : l'API locale ne donne que l'instant
présent et la nuit en cours. Couper internet supprime donc tout l'historique.

Ce serveur n'est donc pas un simple cache d'accélération, c'est la **seule** source
d'historique possible une fois le réveil isolé — et, en échantillonnant plus vite que le
quart d'heure, il fait mieux que l'original. Il résout au passage la lenteur de démarrage :
l'application n'attend plus le réveil, elle lit un historique déjà constitué.

## Architecture cible

| Couche | Choix | Raison |
| --- | --- | --- |
| Matériel | Radxa Zero (originale, Amlogic S905Y2, 2 Go RAM, 8 Go eMMC) | Marge confortable vs Pi Zero 2 W ; eMMC intégrée ; consommation compatible avec l'alimentation par le port USB du Somneo (5 V / 1 A) |
| OS | Armbian 26.2.1 (Trixie, minimal) sur eMMC | Pas de microSD à user en fonctionnement continu |
| Back-end | Python + FastAPI (async) | Le goulot d'étranglement est la latence réseau vers le Somneo, pas le CPU |
| Client Somneo | [`pysomneo`](https://github.com/theneweinstein/pysomneo) | Couvre alarmes, lumière, son et capteurs ; maintenue ; utilisée par l'intégration Home Assistant du même auteur |
| Stockage | SQLite | Suffisant pour un historique capteurs personnel |
| Découverte | SSDP plutôt qu'IP en dur | Résiste aux changements d'adresse en DHCP |

## Licence

**GPL-3.0** — imposée par `pysomneo`, dont ce projet est une œuvre dérivée.

## Contenu du dépôt

À ce stade le dépôt contient l'outillage ayant servi à **installer Armbian sur l'eMMC de la
Radxa Zero**, plus la configuration appliquée à la carte en service. Le serveur FastAPI
lui-même n'est pas encore écrit.

```
LICENSE            GPL-3.0
README.md          ce fichier
AGENTS.md          conventions du dépôt (règles impératives, périmètre, structure)
CLAUDE.md          pointeur de découverte vers AGENTS.md
.gitignore         exclut venv, logs, images et le clone pyamlboot
docs/              référence technique : protocole du Somneo, carte Radxa (voir ci-dessous)
probes/            sondes de mesure de l'appareil et leurs relevés (voir ci-dessous)
radxa-flash/       outillage de flash de l'eMMC (voir ci-dessous)
radxa-config/      configuration de la carte en service, déployée par SSH
```

### `docs/`

`somneo-api.md` — **référence du protocole local du Somneo**, produite le 31 août 2026 en
relevant l'appareil port par port et en décompilant l'application constructeur SleepMapper
(`com.philips.src.hss` 3.22.0-rc.1). Elle donne les 21 ports de l'API, la sémantique des
champs JSON, le schéma d'authentification (inutilisé sur ce firmware) et les pièges de
l'appareil. À lire avant d'écrire du code qui parle au réveil.

`radxa.md` — **référence de la carte** : état du matériel en service, accès SSH, roaming du
firmware WiFi, pilotage de la LED, et les pièges du flash de l'eMMC. À lire avant toute
manipulation de la carte.

### `probes/`

Sondes de diagnostic lancées **depuis la Radxa** — le poste de dev ne peut pas joindre le
réveil. Elles n'ont aucune dépendance : bibliothèque standard seulement, rien à installer sur
la carte. `somneo_probe.py` porte le socle commun (découverte SSDP, `GET` qui ne lève jamais,
lecture du tas).

| Sonde | Ce qu'elle établit |
| --- | --- |
| `discover.py` | Découverte SSDP : l'appareil répond-il, à quelle adresse |
| `burst.py` | Comportement sous rafale sérialisée, et évolution du tas |
| `concurrence.py` | Sérialisé contre concurrent, connexion neuve contre réutilisée |
| `marche.py` | Où se situe la marche : 1, 2 ou 3 requêtes en vol, trois séries par palier |
| `chevauchement.py` | Le motif réel de Home Assistant : une action pendant un rafraîchissement |
| `repro_requests.py` | Le même phénomène dans la pile `requests`, avec la politique de relance de `pysomneo` |
| `bornes_brght.py` | Bornes réelles de `brght`, avec restauration vérifiée |
| `capture.py` | Campagne longue : capteurs, état, suivi de nuit, agrégats de fenêtre |

Le tableau ne reprend que les premières sondes. Il en existe une quarantaine : chacune dit en
tête ce qu'elle établit, et si elle écrit sur l'appareil.

`probes/results/` conserve les relevés bruts, **corps de réponse retirés** : ils portaient le
numéro de série de l'appareil, son adresse MAC et les conditions de la chambre. La valeur
probante — latences, échecs, tas — est intacte. Les résultats chiffrés sont interprétés dans
`docs/somneo-api.md`.

Les **captures longues ne sont pas versionnées** : elles contiennent les mêmes données
personnelles, en continu. Leur place est la base du collecteur.

### `radxa-config/`

| Fichier | Rôle |
| --- | --- |
| `led-schedule.sh` | Éteint la LED verte de la carte de 22 h à 8 h — elle est fixée au dos du réveil, dans une chambre. Installe deux services `gpioset` qui se relaient et leurs minuteries systemd. À lancer en root sur la carte ; idempotent. Les détails (pourquoi la ligne 10 du GPIO AO, pourquoi relâcher la ligne ne rallume pas la LED) sont dans `docs/radxa.md`. |
| `wifi-roamoff.sh` | Pose `options brcmfmac roamoff=1` pour empêcher le firmware WiFi de basculer la carte en 5 GHz de son propre chef. **Se termine par un redémarrage** : le paramètre n'est lu qu'au chargement du module. Déjà appliqué ; utile après une réinstallation. |
| `led-probe.sh` | Diagnostic : force successivement les lignes 10 puis 8 du GPIO AO pour identifier celle qui pilote la LED (elle varie selon la révision de carte). À lancer en vue de la LED. |

### `radxa-flash/`

Scripts issus d'une session de débogage : l'outil officiel Windows (RZ USB Boot Helper)
restait bloqué à l'étape « Start run », et l'eMMC s'est révélée corrompue. Le détail de
l'enquête et des causes est consigné à part (note Obsidian `Somneo-Scraper`).

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

**Dépendance** : `pyamlboot/` est un clone de [superna9999/pyamlboot](https://github.com/superna9999/pyamlboot),
**modifié localement** : les timeouts USB codés en dur (1000 ms, et 100 ms sur une lecture)
sont trop courts via USB/IP, où chaque URB ajoute un aller-retour TCP. Ils sont désormais
pilotés par `AMLBOOT_TIMEOUT` (défaut 15 s), avec `AMLBOOT_RETRIES` et `AMLBOOT_VERBOSE`
en complément. `pyamlboot.py.orig` conserve l'original.

**Loaders Radxa** (téléchargés depuis `dl.radxa.com`, non versionnés) :

| Fichier | Rôle |
| --- | --- |
| `rz-udisk-loader.bin` | Expose l'eMMC en USB Mass Storage — le loader à utiliser pour flasher. |
| `radxa-zero-erase-emmc.bin` | Efface l'eMMC. Indispensable si U-Boot n'arrive plus à l'initialiser. |
| `rz-fastboot-loader.bin` | Expose un gadget fastboot. Surtout utile en diagnostic : il démarre même quand l'eMMC est illisible, ce qui permet d'isoler la panne. |

### Scripts côté Windows

L'écriture de l'image se fait depuis Windows, pas depuis WSL : `usb-storage` boucle sur
des resets à travers USB/IP et ne crée jamais de `/dev/sd*`. Ces scripts vivent
actuellement hors du dépôt, dans `C:\Users\Victor\radxa-flash\` :

| Fichier | Rôle |
| --- | --- |
| `flash_emmc.ps1` | Écrit l'image sur l'eMMC. Refuse toute cible qui ne soit pas un disque USB nommé `*UMS disk*`, de 4 à 16 Go, différent du disque 0. Écrit par blocs de 4 Mo avec une **pause de 150 ms tous les 16 Mo** (voir pièges), consigne sa progression dans `flash_progress.txt` et accepte `-StartOffset` pour reprendre après interruption. |
| `verify_emmc.ps1` | Relit l'eMMC et compare les empreintes SHA256 avec l'image (`-Full` pour l'intégralité). |
| `check_ext4.ps1` | Lit le superbloc ext4 et rapporte le compteur de montages — preuve directe que le board a démarré. |
| `boot_win.py` | Portage du boot bootrom en Python natif Windows. **Ne fonctionne pas** : la pile USB de Windows ne sert jamais l'endpoint bulk IN après `run()`. Conservé comme trace du diagnostic. |

> À consolider dans le dépôt lors d'une prochaine passe.

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
préavis ; il faut `options brcmfmac roamoff=1` pour l'en empêcher. Le détail du diagnostic
est dans `docs/radxa.md`.

## État d'avancement

- [x] Choix de la librairie (`pysomneo`), du matériel et de l'architecture
- [x] Installation d'Armbian sur l'eMMC de la Radxa Zero
- [x] Préconfiguration du WiFi et accès SSH headless (`radxa-zero`, 2,4 GHz)
- [x] Durcissement SSH : authentification par clé ed25519 uniquement, mot de passe et
      login root désactivés (`/etc/ssh/sshd_config.d/99-durcissement.conf`)
- [x] Carte installée au dos du Somneo, alimentée par son port USB, en service continu
- [x] WiFi maintenu en 2,4 GHz (`roamoff=1`) et LED éteinte de 22 h à 8 h
- [x] Découverte SSDP et connexion locale au Somneo — **API accessible sans
      authentification, en lecture comme en écriture**
- [x] Rétro-ingénierie de SleepMapper et cartographie complète de l'API (`docs/somneo-api.md`)
- [x] Cadrage fonctionnel du backend (`.claude/specs/2026-09-06-cadrage-backend-somneo-scraper.md`)
- [x] Campagnes de mesure sur l'appareil — concurrence, cadence, `wusts`, nuits et alarmes ;
      sondes dans `probes/`, relevés dans `probes/results/`
- [x] Contributions à `pysomneo` : commentaires sur les issues #8, #13 et #16, puis les PR
      [#25](https://github.com/theneweinstein/pysomneo/pull/25),
      [#26](https://github.com/theneweinstein/pysomneo/pull/26) et
      [#27](https://github.com/theneweinstein/pysomneo/pull/27)
- [ ] Blocage de l'accès internet du Somneo (pare-feu / VLAN / DNS sinkhole)
- [ ] Conception de l'API interne
- [ ] Service FastAPI + intégration `pysomneo`
- [ ] Historisation SQLite
- [ ] Conteneurisation

## Liens

- Application cliente : SleepMaxxer (Expo / React Native)
- Librairie Somneo : <https://github.com/theneweinstein/pysomneo>
- Intégration Home Assistant de référence : <https://github.com/theneweinstein/somneo>
- Boîtier imprimé : <https://github.com/RoversX/Radxa-Zero-ULTRA-CASE>
