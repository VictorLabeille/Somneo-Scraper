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

- La Radxa Zero tourne sous Armbian 26.2.1 (Trixie, noyau 6.18.15-current-meson64) installé
  sur son eMMC. 1,89 Go de RAM utilisable, rootfs de 6,9 Go.
- Elle est autonome sur le réseau domestique en WiFi 2,4 GHz, hostname `radxa-zero`,
  adresse attribuée par DHCP (non figée).
- Accès SSH par clé uniquement. Depuis le poste de dev : `ssh radxa`.

**Non vérifié — hypothèse centrale du projet encore non testée :**

- **Le Somneo n'a jamais été contacté.** Ni sa découverte SSDP, ni son API REST locale, ni
  `pysomneo` n'ont fait l'objet du moindre essai. Son adresse IP n'est même pas connue.
- On suppose que son API locale continue de répondre **une fois le réveil coupé d'internet**.
  Cette hypothèse conditionne tout le projet et **doit être validée en premier**, avant
  d'écrire la moindre ligne du serveur. Si elle est fausse, l'architecture est à revoir.

Ne rien présumer du comportement du réveil tant que ce test n'est pas fait.

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
```

Le serveur FastAPI n'est pas encore écrit — voir « Périmètre de travail » ci-dessus.

## Conventions

- **Prose en français** (documentation, commentaires, messages de commit).
- Identifiants, noms de fichiers et de variables **en anglais**.
- Python : `pysomneo` pour tout dialogue avec le réveil — ne pas réimplémenter le protocole.
- **Découverte SSDP** plutôt qu'une IP en dur : l'adresse du Somneo peut changer en DHCP.
- SQLite pour l'historique ; pas de base séries temporelles dédiée sans besoin démontré.

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

## Fichiers non versionnés

`.gitignore` exclut le venv, les logs de session, les images système, les loaders `.bin`
(retéléchargeables depuis `dl.radxa.com`) et le clone `radxa-flash/pyamlboot/` (~28 Mo,
dépôt git imbriqué). À convertir en submodule si on veut le versionner proprement — en
tenant compte du patch local ci-dessus.

Les scripts PowerShell d'écriture et de vérification de l'eMMC vivent encore hors du dépôt,
dans `C:\Users\Victor\radxa-flash\`. À consolider ici lors d'une prochaine passe.
