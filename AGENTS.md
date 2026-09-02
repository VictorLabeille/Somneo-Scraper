# AGENTS.md — Guide du dépôt Somneo-Scraper

> Source de vérité unique pour tout agent (humain ou IA) opérant sur ce dépôt.
> Conventions **neutres et model-agnostic** : aucune instruction propre à un modèle ou un
> vendeur. `CLAUDE.md` ne fait que rediriger ici.

## But du dépôt

Serveur local (Python + FastAPI) qui interroge en continu un **Philips Somneo HF3671/01**
via son API REST locale, pour permettre de couper l'accès internet du réveil tout en
conservant ses fonctions. Back-end du projet **SleepMaxxer** (application Expo/React Native
qui remplace SleepMapper).

Voir `README.md` pour l'architecture et l'état d'avancement.

## Périmètre de travail — à lire avant toute modification

Le dépôt contient **deux ensembles sans rapport l'un avec l'autre**. Ne pas les confondre.

| Ensemble | Statut | Consigne |
| --- | --- | --- |
| `radxa-flash/` | **Terminé et archivé** | Outillage ponctuel ayant servi à installer Armbian sur l'eMMC. La carte est en service depuis le 22 août 2026. **Ne pas modifier**, sauf demande explicite de réinstallation du système. |
| `radxa-config/` | **Déployé sur la carte** | Configuration de la carte en service : extinction nocturne de la LED, bande WiFi. Ces scripts ont déjà tourné — le dépôt et la carte doivent rester en phase, modifier un script sans le redéployer les fait diverger silencieusement. |
| `docs/` | **Référence** | `somneo-api.md` : protocole local du Somneo, relevé sur l'appareil et recoupé avec l'APK SleepMapper. Documentation, pas du code. À mettre à jour si un relevé contredit ce qui y est écrit. |
| Le serveur FastAPI | **À écrire** | C'est le travail en cours. Tout nouveau code applicatif va là. |

Concrètement : sauf demande portant explicitement sur la réinstallation de l'OS, `radxa-flash/`
n'est jamais concerné par une tâche. Son contenu est de la documentation d'incident autant que
de l'outillage — les scripts encodent des contournements durement acquis (voir « Pièges connus »),
les modifier à l'aveugle ferait perdre cette information.

Le code applicatif ira dans un **répertoire dédié à la racine**. Sa structure interne reste à
décider par la session qui l'écrira ; une fois arrêtée, la reporter dans ce fichier et dans la
note Obsidian.

## État du matériel — ce qui est acquis, ce qui ne l'est pas

**Acquis et vérifié :**

- La Radxa Zero tourne sous Armbian 26.2.1 (Trixie) installé sur son eMMC. 1,89 Go de RAM
  utilisable, rootfs de 6,9 Go. Noyau `6.18.43-current-meson64` au 23 août 2026 — il a été
  mis à jour depuis l'installation, ne pas s'étonner de le voir bouger.
- Elle est **alimentée par le port USB du Somneo** et fixée à l'arrière du réveil depuis le
  22 août 2026, en fonctionnement continu. Conséquence à peser avant toute manipulation :
  y accéder physiquement suppose de défaire l'installation, et la carte n'a ni console série
  ni écran. Préférer systématiquement ce qui est réversible à chaud à ce qui touche au
  chemin de boot.
- Elle est autonome sur le réseau domestique en WiFi 2,4 GHz, hostname `radxa-zero`,
  adresse attribuée par DHCP (non figée — 192.168.1.198 au 23 août 2026, à ne jamais coder
  en dur).
- Accès SSH par clé uniquement. Depuis le poste de dev : `ssh radxa`.
- Sa LED verte est éteinte de 22 h à 8 h — voir « LED d'alimentation » plus bas.

**Acquis depuis le 31 août 2026 — l'hypothèse centrale est validée :**

- Le Somneo répond en SSDP (`ST: urn:philips-com:device:DiProduct:1`) et son API REST locale
  a été relevée port par port. C'est un **HF3671/01**, nom de code interne *BrightEyes*.
- **Aucune authentification n'est exigée**, ni en lecture ni en écriture : un `PUT` de test
  est passé en `200` sans en-tête `Authorization`, le port `pairing` répond `501` et
  `device.allowpairing` vaut `false`. Le pilotage local est donc acquis sans compte Philips.
- Son adresse est en DHCP (192.168.1.97 au 31 août 2026) : **passer par SSDP**, jamais par
  une IP en dur.

**Ce que la rétro-ingénierie a changé dans le projet — à lire avant d'écrire le serveur :**

- **L'API locale n'a aucune mémoire.** Aucun endpoint d'historique n'existe : `wusrd` ne
  donne que l'instant et des moyennes, `wungt` que la nuit en cours. L'historique de
  SleepMapper vient du **cloud** Philips, alimenté par le port `dataupload` du réveil.
- Donc couper internet **supprime tout l'historique de l'application constructeur**.
  `Somneo-Scraper` n'est pas un cache d'accélération : c'est la **seule** source
  d'historique possible après isolement, et sa base SQLite devient la mémoire du réveil.
- `pysomneo` ne couvre que 11 des 21 ports, et **ignore `wungt`** — donc tout le suivi de
  coucher/lever. Des appels directs en complément seront nécessaires.

Tout le détail (ports, sémantique des champs, pièges) est dans **`docs/somneo-api.md`**.
S'y référer avant d'écrire du code qui parle au réveil, plutôt que de re-sonder l'appareil :
son tas est de ~25 ko libres et il tombe en timeout sous une rafale de requêtes.

## Ordre de construction — arrêté le 2 septembre 2026

Le code applicatif n'est pas encore écrit. L'ordre suivant a été décidé et **ne doit pas être
réarbitré sans raison** :

1. **Cadrage fonctionnel de SleepMaxxer d'abord.** La forme de l'API interne doit être dictée
   par ce dont l'application a besoin, pas devinée depuis le protocole du réveil. Commencer
   par le backend reviendrait à inventer un contrat que le client ne demandera pas.
2. **Puis le serveur** (découverte SSDP, collecte, SQLite, endpoints FastAPI).
3. **Puis les contributions à `pysomneo`** (voir `docs/somneo-api.md` §8). Contribuer *après*
   avoir construit, et non avant : l'usage réel fait remonter les vrais pièges, et la PR en
   sort meilleure que si elle était écrite d'après la seule lecture de l'APK décompilé.

Stratégie de collecte retenue, à ne pas redécouvrir : lire **`wusrd` pour l'instantané** et
**`dataupload/{temp,hum,snd,lux}.1/data` pour les extrema et histogrammes de la fenêtre de
15 minutes**. Cela capture les pics sans interroger l'appareil à haute fréquence — ce que son
tas de ~25 ko ne supporterait pas. Sérialiser les appels et les espacer d'environ 200 ms.

Le répertoire applicatif reste à créer à la racine ; sa structure interne sera arrêtée par la
session qui l'écrira, puis **reportée ici et dans la note Obsidian**.

## Note Obsidian associée

Ce projet est catalogué dans le vault Obsidian personnel :
`Projets/Somneo-Scraper.md` (`type: project`).

**À faire après tout changement significatif** : mettre à jour la note — statut, périmètre,
décisions d'architecture, section « État d'avancement », et le « Journal » en fin de note.

Ne **pas** éditer à la main les champs `created`, `updated` et `repo` du frontmatter : ils
sont dérivés du git par `_scripts/sync-projects.py` dans le vault. Relancer ce script après
une activité git plutôt que de saisir les dates.

Le projet frontend a sa propre note, `Projets/SleepMaxxer.md`, liée par `[[wikilink]]`.
Une décision qui touche le contrat entre les deux (format des données, endpoints) doit être
reportée dans les deux notes.

## Contrainte de licence — importante

Le dépôt est sous **GPL-3.0**, et ce n'est pas un choix esthétique : il dépend de
[`pysomneo`](https://github.com/theneweinstein/pysomneo), elle-même GPL-3.0. Ce projet en
est une œuvre dérivée.

Conséquences à respecter :

- Ne pas introduire de dépendance dont la licence est incompatible avec la GPL-3.0.
- Ne pas relicencier le dépôt sans avoir d'abord retiré `pysomneo`.
- Toute redistribution (image, conteneur, binaire) doit s'accompagner des sources.

## Structure

```
README.md          présentation, architecture, procédure de flash
AGENTS.md          ce fichier
CLAUDE.md          pointeur vers AGENTS.md
LICENSE            GPL-3.0
radxa-flash/       outillage d'installation d'Armbian sur l'eMMC (ponctuel, pas du runtime)
radxa-config/      configuration de la carte en service, à déployer par SSH (LED, WiFi)
docs/              référence protocolaire issue de la rétro-ingénierie (somneo-api.md)
```

Le serveur FastAPI n'est pas encore écrit — voir « Périmètre de travail » ci-dessus.

## Conventions

- **Prose en français** (documentation, commentaires, messages de commit).
- Identifiants, noms de fichiers et de variables **en anglais**.
- Python : `pysomneo` pour tout dialogue avec le réveil — ne pas réimplémenter le protocole.
- **Découverte SSDP** plutôt qu'une IP en dur : l'adresse du Somneo peut changer en DHCP.
- SQLite pour l'historique ; pas de base séries temporelles dédiée sans besoin démontré.
- **Le dépôt est public.** Ne jamais y versionner ce qui est propre à l'appareil ou au
  réseau : clé du port `security`, numéro de série, adresses MAC, SSID, mots de passe.
  `docs/somneo-api.md` est rédigé sous cette contrainte — la tenir en le complétant.
- Espacer les appels au réveil (~200 ms) et les sérialiser : il tombe en `500 Timeout`
  sous une rafale.

## Pièges connus — `radxa-flash/`

Ces points ont coûté une session entière de débogage. Les relire avant de toucher au flash.

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

## Accès au board

Une fois Armbian installé : hôte `radxa-zero`, joignable en SSH sur le WiFi domestique
(bail DHCP, IP non figée). Le WiFi est configuré dans l'image avant écriture, via
`/etc/wpa_supplicant/wpa_supplicant-wlan0.conf` — NetworkManager **n'est pas installé** sur
l'image minimale, ne pas perdre de temps à le configurer. La bande est restreinte au
2,4 GHz par `freq_list` : portée et stabilité priment sur le débit, le trafic se limitant à
des requêtes REST de quelques Ko vers le Somneo.

**Mais `freq_list` ne suffit pas — piège vérifié le 23 août 2026.** La carte s'est retrouvée
associée au BSS 5 GHz de la box (canal 112, DFS, -75 dBm) alors que l'option était toujours
présente et active dans la configuration. En cause : le **roaming interne du firmware** du
CYW43455 (`brcmfmac`), qui change de point d'accès de lui-même sans passer par
wpa_supplicant — donc sans que `freq_list` ait son mot à dire. La signature est nette dans
`journalctl -u wpa_supplicant@wlan0` : les associations subies arrivent **sans** la ligne
« Trying to associate » qui précède toujours celles que wpa_supplicant décide.

```
Trying to associate with 62:… (SSID='…' freq=2437 MHz)   <- wpa_supplicant : 2,4 GHz
Associated with 62:…
Associated with 72:…                                      <- firmware : 5 GHz, sans préavis
```

La parade est `options brcmfmac roamoff=1` dans `/etc/modprobe.d/brcmfmac.conf`, posé par
`radxa-config/wifi-roamoff.sh` et effectif depuis le redémarrage du 23 août 2026 : retour au canal 6 à **-57 dBm** au lieu de -75, et
72 Mbit/s au lieu de 27. Le module n'étant pas embarqué dans l'initramfs (`lsinitramfs` le
confirme), aucun `update-initramfs` n'est nécessaire — rien à toucher dans le chemin de boot.

Si le symptôme réapparaît, chercher d'abord une association « nue » dans le journal avant de
suspecter `wpa_supplicant-wlan0.conf` : cette configuration-là, elle, est correcte.

Le mot de passe WiFi n'est jamais stocké en clair : il est converti en clé dérivée
PBKDF2-HMAC-SHA1 avant d'être écrit dans l'image.

**SSH durci** : authentification par clé ed25519 uniquement, mot de passe et login root
désactivés via `/etc/ssh/sshd_config.d/99-durcissement.conf`. Depuis le poste de dev,
raccourci `ssh radxa` (entrée dans `~/.ssh/config`).

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

## Fichiers non versionnés

`.gitignore` exclut le venv, les logs de session, les images système, les loaders `.bin`
(retéléchargeables depuis `dl.radxa.com`) et le clone `radxa-flash/pyamlboot/` (~28 Mo,
dépôt git imbriqué). À convertir en submodule si on veut le versionner proprement — en
tenant compte du patch local ci-dessus.

Les scripts PowerShell d'écriture et de vérification de l'eMMC vivent encore hors du dépôt,
dans `C:\Users\Victor\radxa-flash\`. À consolider ici lors d'une prochaine passe.
