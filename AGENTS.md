# AGENTS.md — Guide du dépôt Somneo-Scraper

> Source de vérité unique pour tout agent (humain ou IA) opérant sur ce dépôt.
> Conventions **neutres et model-agnostic**. `CLAUDE.md` ne fait que rediriger ici.
>
> Ce fichier **oriente, il ne documente pas**. Le fonctionnel et les procédures sont dans
> `README.md`, les faits techniques dans `docs/`, les décisions dans la note Obsidian.

## But du dépôt

Serveur local (Python + FastAPI) qui interroge en continu un **Philips Somneo HF3671/01**
via son API REST locale, pour permettre de couper l'accès internet du réveil tout en
conservant ses fonctions. Back-end de **SleepMaxxer** (l'application qui remplace
SleepMapper). Voir `README.md`.

## Où trouver quoi — à lire avant d'écrire

| Question | Fichier |
| --- | --- |
| Que fait le projet, comment flasher, où en est l'avancement | `README.md` |
| Ce que le collecteur doit être, et pourquoi | `.claude/specs/2026-09-06-cadrage-backend-somneo-scraper.md` |
| Comment une mesure sur l'appareil a été obtenue, et comment la rejouer | `probes/` |
| Protocole du Somneo : 21 ports, sémantique des champs, stratégie de collecte, pièges de l'appareil | `docs/somneo-api.md` |
| La carte Radxa : état en service, WiFi, SSH, LED, pièges du flash | `docs/radxa.md` |
| Ce que l'application attend du backend, et pourquoi | `.claude/specs/2026-09-05-spec-fonctionnelle-sleepmaxxer.md` **du dépôt SleepMaxxer** |
| Décisions, arbitrages, ordre de construction, journal | Note Obsidian `Projets/Somneo-Scraper.md` |

## Périmètre de travail — à lire avant toute modification

Le dépôt contient **deux ensembles sans rapport l'un avec l'autre**. Ne pas les confondre.

| Ensemble | Statut | Consigne |
| --- | --- | --- |
| `radxa-flash/` | **Terminé et archivé** | Outillage ponctuel ayant servi à installer Armbian sur l'eMMC. La carte est en service depuis le 22 août 2026. **Ne pas modifier**, sauf demande explicite de réinstallation du système. |
| `radxa-config/` | **Déployé sur la carte** | Configuration de la carte en service. Ces scripts ont déjà tourné — modifier un script sans le redéployer fait diverger le dépôt et la carte, silencieusement. |
| `docs/` | **Référence** | Documentation, pas du code. À mettre à jour si un relevé contredit ce qui y est écrit. |
| `probes/` | **Outillage vivant** | Sondes de mesure, lancées depuis la Radxa. Elles fondent les chiffres publiés dans `docs/` et ceux qu'on avancera en amont : une mesure qu'on ne peut plus rejouer ne se défend pas. `bornes_brght.py` est la seule qui écrive. |
| Le serveur FastAPI | **À écrire** | C'est le travail en cours. Tout nouveau code applicatif va là. |

Sauf demande portant explicitement sur la réinstallation de l'OS, `radxa-flash/` n'est jamais
concerné par une tâche. Son contenu est de la documentation d'incident autant que de
l'outillage : les scripts encodent des contournements durement acquis, les modifier à
l'aveugle ferait perdre cette information.

Le code applicatif ira dans un **répertoire dédié à la racine**. Sa structure interne reste à
décider par la session qui l'écrira ; une fois arrêtée, la reporter ici et dans la note.

## Règles impératives

**Vis-à-vis du réveil**

- **Toujours passer par la découverte SSDP**, jamais par une IP en dur : l'adresse du Somneo
  change en DHCP. Même règle pour la carte.
- **Ne jamais lancer `pkill -f <motif>` depuis SSH si le motif figure dans la commande
  envoyée.** `pkill` matche sa propre ligne de commande et **coupe la session** — erreur faite
  deux fois le 2026-09-06. Déposer un script sur la carte et l'appeler par son nom.
- **Comparer les heures en secondes depuis l'époque, jamais en texte.** `[ "$(date +%H%M)" \< "0730" ]`
  est **faux** à 22 h 40 : `"2240" < "0730"` en comparaison de chaînes. Une minuterie écrite
  ainsi se déclenche immédiatement — c'est ce qui a coupé la capture d'une nuit.
- **Ne jamais suspendre une sonde par `SIGSTOP` pour en lancer une autre.** Le processus est
  figé **au milieu d'une requête** et laisse une connexion à moitié ouverte sur un appareil qui
  n'en sert qu'une seule — on fabrique soi-même la panne qu'on mesure. Arrêter proprement
  (`SIGTERM`, les sondes le gèrent) puis relancer : le journal est en ajout, rien n'est perdu.
- **Toujours sérialiser les appels au réveil** et les espacer d'environ 200 ms. Il tombe en
  `500 Timeout` sous une rafale — ~25 ko de tas libre. C'est le matériel, pas un bug.
- **`pysomneo` pour tout dialogue avec le réveil** — ne pas réimplémenter le protocole. Il ne
  couvre que 11 des 21 ports : compléter par des appels directs, pas par un remplacement.
- **Ne jamais chercher d'historique côté appareil** : l'API locale n'a aucune mémoire, toutes
  les variantes ont été testées. La base SQLite du collecteur en est la **source** — l'app en
  tient une copie sur le téléphone depuis le 2026-09-06, mais c'est le collecteur qui fait
  autorité en cas de divergence.
- **Appareil en service dans une chambre.** Une écriture à la fois, choisie sans effet
  observable, jamais sur les alarmes ou la lumière le soir.

**Vis-à-vis de la carte**

- **Préférer ce qui est réversible à chaud à ce qui touche au chemin de boot.** La carte est
  fixée au dos du réveil, sans console série ni écran : y accéder physiquement suppose de
  défaire l'installation.
- **Valider par `sshd -t` avant tout `systemctl reload ssh`** — une erreur de syntaxe coupe
  définitivement l'accès.
- **Le poste de dev ne peut pas découvrir le réveil** : WSL2 est derrière un NAT que le
  multicast SSDP ne franchit pas. Toute sonde part de la Radxa, par SSH. Ne pas relancer
  cette impasse.

**Vis-à-vis du projet**

- **L'API interne est dictée par le cadrage de SleepMaxxer**, pas devinée depuis le protocole
  du réveil. **Le lire avant de concevoir le schéma** : il porte des exigences qui contraignent
  la base, et qu'on ne peut pas ajouter après coup. Décision du 2 septembre 2026, argumentée
  dans la note Obsidian.
- **La remise à l'heure du réveil est une fonction de ce serveur**, pas de l'application :
  l'horloge dérive une fois le cloud coupé, et seul le collecteur tourne en continu.
- **SQLite pour l'historique** ; pas de base séries temporelles dédiée sans besoin démontré.

## Contrainte de licence — importante

Le dépôt est sous **GPL-3.0**, et ce n'est pas un choix esthétique : il dépend de
[`pysomneo`](https://github.com/theneweinstein/pysomneo), elle-même GPL-3.0. Ce projet en
est une œuvre dérivée.

- Ne pas introduire de dépendance dont la licence est incompatible avec la GPL-3.0.
- Ne pas relicencier le dépôt sans avoir d'abord retiré `pysomneo`.
- Toute redistribution (image, conteneur, binaire) doit s'accompagner des sources.

## Dépôt public

**Ne jamais versionner un relevé brut sans en avoir retiré les corps de réponse.** Les ports
`device` et `wifiui` livrent le numéro de série et l'adresse MAC, et `wusrd` livre les
conditions de la chambre. `probes/results/` ne contient que des relevés nettoyés ; les captures
longues ne sont pas versionnées du tout.

**Les journaux de balayage ne font pas exception** — contrairement à ce qui était écrit ici
jusqu'au 2026-09-07. Ils sont presque entièrement faits de noms de ports et de codes `422`,
mais ils enregistrent **le corps des ports qui répondent** : sur 17 576 lignes, les 13 du
balayage `wu` portaient un relevé complet de `wusrd`, soit les conditions de la chambre.
Retirer les corps avant de verser dans `probes/results/`, et ne se fier ni à la taille du
fichier ni à son extension pour juger de ce qu'il contient.

Le `sans_secrets()` des sondes **ne suffit pas** : il masque `serial`, `macaddress`, `ssid` et
les clés, mais **laisse passer les capteurs** (`mslux`, `mstmp`…), qui décrivent la chambre.
Relire le relevé avant de le verser, ne pas se reposer sur la fonction.

Ne jamais versionner ce qui est propre à l'appareil ou au réseau : clé du port `security`,
numéro de série, adresses MAC, SSID, mots de passe. `docs/somneo-api.md` est rédigé sous
cette contrainte — la tenir en le complétant.

## Structure

```
README.md          présentation, architecture, procédure de flash, avancement
AGENTS.md          ce fichier
CLAUDE.md          pointeur vers AGENTS.md
LICENSE            GPL-3.0
docs/              somneo-api.md (protocole du réveil) · radxa.md (la carte)
probes/            sondes de mesure de l'appareil, et results/ leurs relevés nettoyés
radxa-flash/       outillage d'installation d'Armbian sur l'eMMC (ponctuel, archivé)
radxa-config/      configuration de la carte en service, déployée par SSH
```

`.gitignore` exclut le venv, les logs de session, les images système, les loaders `.bin`
(retéléchargeables depuis `dl.radxa.com`) et le clone `radxa-flash/pyamlboot/` (~28 Mo, dépôt
git imbriqué, **patché localement** — voir `docs/radxa.md`). Les scripts PowerShell d'écriture
de l'eMMC vivent encore hors du dépôt, dans `C:\Users\Victor\radxa-flash\`.

## Conventions d'écriture

- **Prose en français** : documentation, commentaires, messages de commit.
- **Identifiants, noms de fichiers et de variables en anglais.**

## Note Obsidian associée

`Projets/Somneo-Scraper.md` (`type: project`) dans le vault personnel. **À mettre à jour après
tout changement significatif** : statut, périmètre, décisions, « État d'avancement », journal.

Ne **pas** éditer à la main `created` / `updated` / `repo` : dérivés du git par
`_scripts/sync-projects.py`, à relancer après un push.

Le projet frontend a sa propre note, `Projets/SleepMaxxer.md`. Une décision touchant le
**contrat entre les deux** (format des données, endpoints, répartition des responsabilités)
se reporte dans les deux notes et dans l'`AGENTS.md` de l'autre dépôt.
