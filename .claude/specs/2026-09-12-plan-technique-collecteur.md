# Plan technique — le collecteur

> Statut : **validé** le 2026-09-12, onze points tranchés un par un (§11) — sous réserve des
> préalables P1 à P3 (§1) · Date : 2026-09-12 · Répond au cadrage du 2026-09-06
> (`2026-09-06-cadrage-backend-somneo-scraper.md`)

Le cadrage dit **ce que** le collecteur doit être ; ce document dit **comment** il sera
construit. Il ne réarbitre aucune exigence du cadrage. Quand la mesure sur l'appareil impose
une manière de faire, il le dit, et renvoie à `docs/somneo-api.md`. Quand un choix reste
ouvert, il est listé au §11 au lieu d'être tranché en silence.

Aucune ligne de code n'existe encore. Tant que c'est vrai, ce plan se modifie sur place.

---

## 0. Ce qui est déjà tranché

| Question | Décision | Source |
| --- | --- | --- |
| Quelle `pysomneo` | **6.0 async** (branche `ai-improvements`), épinglée au commit `13ec0c5` du fork, soit `365d313` + `limit=1` (PR #26). Bascule sur la version PyPI dès que la 6.0.0 sort avec le correctif | Victor, 2026-09-12 |
| Forme du déploiement | **Service systemd + venv**, pas de conteneur | Victor, 2026-09-12 |
| Captures du 06 au 12/09 | **Pas importées.** La base commence au démarrage du collecteur ; les JSONL restent sur la carte | Victor, 2026-09-12 |
| Ce qui contraint l'appareil | La **concurrence**, pas la fréquence : une seule connexion TLS en vol, garantie par construction | Mesure, `docs/somneo-api.md` §7 |
| Cadence soutenable | 60, 30, 15 puis 5 s, 45 min chacun, zéro échec, tas immobile | Mesure du 2026-09-07 |
| Abonnement UDP | Mort, sept formes essayées. Tout se fait par interrogation | Mesure du 2026-09-07 |
| Historique côté appareil | Inexistant. Rien ne se rattrape après coup | Mesure, §5 |

---

## 1. Préalables — à mesurer avant d'écrire le code qui en dépend

Trois fonctions du cadrage reposent sur des écritures que personne n'a encore mesurées ici.
Écrire leur code avant, ce serait coder une supposition.

### P1. Le geste de coucher : `wungt` en écriture

Le cadrage (§5) veut qu'un appui fait pendant que le réveil ne répond pas soit « posé dans le
réveil avec l'heure de l'appui ». Or ce qu'on sait de `wungt` vient de SleepMapper, qui écrit
`{"night": true}` **au moment de l'appui** : `tg2bd` prend alors l'heure de la requête. Rien ne
dit qu'on puisse écrire `tg2bd` soi-même.

À établir, depuis la carte :

1. `PUT wungt {"night": true}` : `tg2bd` prend-il l'heure de la requête, à la seconde ?
2. `PUT wungt {"night": true, "tg2bd": "<ISO 8601>"}` : le champ est-il accepté (`200`), refusé
   (`422`), ou accepté et ignoré ? Relire après chaque écriture.
3. `PUT wungt {"night": false}` : la session se ferme-t-elle, et que vaut `tendb` ?

**Si `tg2bd` n'est pas inscriptible**, la décision du cadrage change de forme sans changer de
fond. L'heure de l'appui vit dans la base du collecteur, qui fait autorité, et le réveil reçoit
l'heure du retour. Le geste n'est pas perdu, mais le réveil cesse d'être le « dépositaire de
l'heure » dans ce cas précis. À reporter alors dans les deux cadrages.

Effet observable : aucun, ni lumière ni son. Mais l'essai ouvre une session de nuit dans le
réveil, et on ne sait pas si une session ouverte change autre chose. **Jamais pendant une
vraie nuit** — `wungt` ne tient qu'une session, un essai écraserait la vraie — ni à quelques
heures d'une alarme. Session refermée aussitôt, relue.

### P2. La remise à l'heure : `PUT products/0/time`

Le port `time` expose `datetime`, `dst`, `dstchangeover`, `dstoffset`, `timezone` et `calday`.
L'appareil connaît déjà la prochaine bascule (`dstchangeover` = 25 octobre 2026, 3 h). Ce qui
n'est pas établi :

1. quels champs l'écriture accepte (`datetime` seul ? le fuseau ? la bascule ?) ;
2. si l'écriture se reflète dans le port `wutim` (l'horloge décomposée) autant que dans `time` ;
3. si `wutms.tmupd`, `tmsyn` ou `dstwu` bougent en conséquence.

Effet observable : l'afficheur change d'heure. Écrire l'heure juste, à une seconde près, rend
l'effet invisible. **En journée, jamais le soir.**

Tant que P2 n'est pas fait, le collecteur **mesure** la dérive sans rien corriger (§6).

### P3. La lecture d'un profil d'alarme

`pysomneo` lit le détail d'un profil en le sélectionnant : `PUT wualm {"prfnr": n}`
(`modify_alarm_details`, appelé par `get_alarm_details`). La racine `wualm` porte alors `prfnr`
= n et le détail du profil dans `prfwu`. À ne pas confondre avec `PUT wualm/prfwu`, qui
**configure** un profil (`modify_alarm_wake_up_configuration`). On s'attend à ce que la
sélection ne change rien d'autre ; ce n'est pas vérifié. Effet observable attendu : aucun.
**Jamais pendant une alarme.**

Les protocoles sont dans la docstring de chaque sonde, commitée avant la mesure :
`probes/ecriture_wungt.py` (P1), `probes/selection_profil.py` (P3).

---

## 2. Architecture

Un seul processus Python, lancé par systemd, qui porte à la fois l'API (FastAPI, servie par
uvicorn) et les tâches de fond : collecte, suivi des nuits, horloge, sauvegarde, découverte.

### La passerelle, seul accès au réveil

Tout dialogue avec l'appareil passe par **un seul objet**, `DeviceGateway`, qui détient :

- l'unique session `pysomneo` (donc l'unique connexion TLS, réutilisée) ;
- un `asyncio.Lock` : **une requête en vol, jamais deux**, quel que soit le nombre d'appelants ;
- l'espacement d'environ 200 ms entre deux requêtes — prudence, pas nécessité (§7 de la doc) ;
- la file d'attente, dans l'ordre d'arrivée, les commandes de l'utilisateur passant devant la
  prochaine lecture de collecte.

**La sérialisation tient par construction, pas par discipline.** Aucun autre module n'importe
`pysomneo` ; un test le vérifie. Et comme notre verrou est au-dessus de la bibliothèque, le
pool d'aiohttp n'a jamais de file : le défaut observé au banc de la PR #26 (à 12 tâches, une
requête en file expire dans `timeout.connect`) ne peut pas se produire ici.

**Un seul worker uvicorn.** Un second processus ouvrirait une seconde connexion, et
l'appareil ferait tomber les deux. C'est une contrainte, pas un réglage : elle est codée en dur.

### Ce qui passe par la bibliothèque, et ce qui passe à côté

- **Écritures** : par les méthodes de `Somneo` (`toggle_light`, `set_alarm`, `set_sunset`…),
  qui portent la connaissance du protocole — construction des profils d'alarme, noms des
  thèmes. C'est la règle de l'`AGENTS.md` : ne pas réimplémenter le protocole.
- **Lectures** : par `SomneoClient`, qui rend les corps JSON bruts. Le collecteur conserve ce
  que l'appareil dit, pas ce que la bibliothèque en interprète (`somneo_status` mappe encore
  `2` sur `sunset`).
- **Ports non couverts** — `wungt`, `dataupload/*/data`, `wutim`, et le produit 0 (`time`,
  `backend`, `transport`) : par `SomneoClient._internal_call`, **sur la même session**. Un
  chemin absolu (`/di/v1/products/0/time`) remplace le préfixe du produit 1.

**Coût accepté** : deux accès privés — `Somneo._client` et `SomneoClient._internal_call`. Ils
sont confinés à la passerelle et épinglés par commit, et un test de contrat casse s'ils
disparaissent. Si la 6.0.0 publiée les renomme, on le saura au premier `pytest`, pas en
production.

**Les réessais de `pysomneo`.** `SomneoSession.request` retente trois fois les erreurs de
connexion, avec attente croissante et recréation de session. Un `500 Timeout`, lui, n'est
**pas** retenté : `raise_for_status` est appelé hors de la boucle, il remonte tel quel. C'est ce
qui rend mesurable le critère du cadrage (« 24 h sans un seul `500` »). Les échecs absorbés par
les réessais sont comptés en écoutant le journal DEBUG de `pysomneo.api`, comme dans
`probes/banc_limit.py`.

### Découverte

- **Le réveil, par SSDP** — le code de `probes/somneo_probe.py`, avec un correctif : sur une
  carte hors réseau, `discover()` lève `OSError: Network is unreachable` au lieu de rendre
  `None`. C'est ce qui a tué la capture des dizaines de fois (44 relances le 2026-09-12, sur
  deux salves de déconnexions WiFi). Le
  collecteur distingue les deux cas, ils n'ont pas la même cause (§3).
- **Redécouverte** après cinq échecs de connexion consécutifs, puis à intervalle croissant
  (30 s → 10 min). Aucune adresse n'est jamais retenue comme définitive.
- **Le collecteur lui-même, par mDNS** (`zeroconf`, LGPL-2.1-or-later, compatible GPL-3.0) :
  il annonce un service **`_somneo-scraper._tcp`**, avec `api=v1` en enregistrement TXT. Ce nom
  est un **contrat** avec SleepMaxxer, arrêté le 2026-09-12. Le premier nom proposé,
  `_somneo-collector._tcp`, était invalide : `zeroconf` refuse en mode strict un nom de service
  de plus de 15 octets (RFC 6763), et celui-là en compte 16.

### Dépendances

| Paquet | Licence | Rôle |
| --- | --- | --- |
| `pysomneo` (fork épinglé) | GPL-3.0 | dialogue avec le réveil |
| `aiohttp` 3.14.3 | Apache-2.0 et MIT | tiré par `pysomneo`, version de Home Assistant et des bancs |
| `fastapi`, `pydantic` | MIT | API et validation |
| `uvicorn` | BSD-3-Clause | serveur |
| `zeroconf` | LGPL-2.1-or-later | annonce mDNS |
| `pytest`, `pytest-asyncio` (tests) | MIT, Apache-2.0 | tests hors appareil |

Toutes compatibles avec la GPL-3.0. Pas d'`aiosqlite` : `sqlite3` de la bibliothèque standard
suffit (§3). Les roues `aarch64` pour Python 3.13 existent pour les trois paquets compilés
(`aiohttp`, `pydantic-core`, `zeroconf`) — vérifié sur PyPI le 2026-09-12.

### Arborescence — arrêtée le 2026-09-12

```
collector/
  pyproject.toml
  src/somneo_collector/
    __main__.py        point d'entrée : uvicorn programmatique, un seul worker
    config.py          cadences, chemins, seuils — fichier TOML sur la carte, hors dépôt
    gateway.py         SEUL module qui importe pysomneo : verrou, espacement, file
    discovery.py       SSDP (le réveil) et mDNS (le collecteur)
    store/             schema.sql, migrations numérotées, accès à la base
    collect.py         planificateur des lectures
    nights.py          machine à états des sessions de nuit
    clock.py           mesure de dérive, remise à l'heure
    backup.py          sauvegarde en rotation
    api/               routes et modèles
  tests/
    fixtures/          réponses de forme réelle, valeurs remplacées (voir §9)
  deploy/
    somneo-collector.service
    install.sh
```

---

## 3. La base

**SQLite, un seul fichier, mode WAL, `synchronous=FULL`.** La carte est alimentée par le port
USB d'un réveil : la coupure de courant en pleine écriture est le mode de panne le plus
probable. Au débit d'écriture du collecteur, quelques lignes par minute, `FULL` ne coûte rien.

**Un seul écrivain** — la boucle de collecte et les commandes, sérialisées — et des lecteurs
concurrents : les routes de l'API, qui ouvrent chacune leur connexion. C'est le cas d'usage
de WAL.

### Trois principes

1. **Tout horodatage est en UTC** (millisecondes depuis l'époque), avec le décalage local à
   l'instant de la mesure quand il compte. Une durée se calcule en UTC : une nuit qui traverse
   la bascule reste juste sans rien faire.
2. **Stocker la source, dériver le reste.** Le journal d'événements du cadrage (alarme,
   rappel, lumière, coucher de soleil) est le **journal des changements de `wusts`** : on
   stocke les valeurs brutes, on dérivera les événements quand un besoin existera. Aucune règle
   de dérivation n'est figée dans les données.
3. **Une valeur inchangée ne se répète pas ; la couverture dit qu'on regardait.** Pour les
   ports d'état, seul un changement crée une ligne. Pour savoir si « pas de ligne » veut dire
   « rien n'a changé » ou « on ne regardait pas », il y a le journal d'indisponibilité. Pour
   les agrégats, c'est exactement l'information que l'appareil donne : il n'annonce pas ses
   bascules de fenêtre, et deux fenêtres identiques sont indiscernables (§5 de la doc).

### Tables

| Table | Contenu | Écriture |
| --- | --- | --- |
| `device` | un appareil par ligne : modèle, firmware, première et dernière vue | à la découverte ; un nouveau numéro de série = une rupture datée, la série continue |
| `reading` | les points mesurés de `wusrd` : 8 colonnes typées, `NULL` pour une grandeur absente | chaque lecture |
| `window_aggregate` | `dataupload/{temp,hum,snd,lux}.1/data` : moyenne, min, max, histogrammes (JSON), heure de **première lecture** | au changement |
| `port_change` | corps bruts de `wusts`, `wungt`, `wulgt`, `wudsk`, `wualm/*`, `wutms`, `backend`, `transport`, `device`, `files/*` | au changement |
| `outage` | périodes d'indisponibilité : début, fin, cause, nombre d'échecs | à chaque transition |
| `heartbeat` | une ligne, réécrite chaque minute | sert à borner un arrêt du collecteur |
| `night` | une session : coucher, lever, origine de chaque heure, état, valeurs brutes de `wungt` | machine à états (§5) |
| `night_correction` | corrections de l'app, en ajout seul | API |
| `pending_gesture` | appuis reçus pendant que le réveil ne répondait pas | API, puis passerelle |
| `clock_check` | heure de la carte, heure du réveil (`time` et `wutim`), écart, correction faite ou non | chaque contrôle (§6) |

`reading` et `window_aggregate` sont **deux tables**, comme le cadrage le demande : une valeur
mesurée et une valeur déduite ne se rangent pas ensemble. Un agrégat n'est **jamais horodaté à
sa fenêtre** : elle est inconnue, et publiée avec une fenêtre de retard. On garde l'heure où on
l'a vue.

**Les causes d'indisponibilité.** Le cadrage en nomme trois : collecteur arrêté, réveil
injoignable, appareil saturé. La capture en a montré une quatrième, distincte et fréquente :
**la carte hors réseau** (`Network is unreachable`, WiFi tombé). Du point de vue du trou dans
la courbe, ce n'est pas le réveil qui manque : c'est la carte. Retenue le 2026-09-12 (§11) et
reportée dans les deux cadrages : l'app la nomme comme les trois autres.

### Volume

Une ligne de `reading` pèse **95 octets**, index compris — mesuré le 2026-09-12 sur un mois
synthétique de 43 200 lignes (la table ci-dessus, index sur `ts` et `seq`, après `VACUUM`).
Soit **~50 Mo par an à la minute**, ~100 Mo à 30 s, ~200 Mo au palier de 15 s. Les changements
d'état et les agrégats ajoutent quelques dizaines de Mo au plus. Sur les 5,2 Go libres du
rootfs, cela laisse une vingtaine d'années au palier le plus fin : la purge reste hors de
propos, comme le disait le cadrage.

### Sauvegarde et disque plein

- **Chaque jour, en journée**, par l'API de sauvegarde de `sqlite3` (`Connection.backup`), qui
  produit une copie cohérente sans arrêter l'écrivain. `PRAGMA quick_check` sur la copie avant
  de la garder.
- **Rotation : 7 quotidiennes et 4 hebdomadaires, compressées en gzip, sous un plafond de
  1,5 Go** (tranché le 2026-09-12). Au-delà, la plus ancienne part ; la plus récente est
  toujours gardée, et si elle-même ne tient pas, l'état du système le dit. Sans plafond, au
  palier de 15 s, les onze copies rempliraient l'eMMC en cinq ans environ — compression
  mesurée à 2,6× au pire, sur la base synthétique du §3 (des valeurs aléatoires se compressent
  moins bien que de vraies mesures).
- **Disque plein** (`database or disk is full`) : la collecte s'arrête proprement, l'état du
  système le dit, l'API continue de servir l'historique.

### Numéro de séquence

Toute ligne exposée à l'app porte un `seq`, un compteur global et croissant, repris à chaque
insertion **et à chaque modification**. C'est lui qui permet au rattrapage de ramener une nuit
corrigée après copie (§7).

---

## 4. La collecte

Un planificateur unique, sur l'horloge monotone. Chaque tâche a sa période et sa prochaine
échéance. **Après une interruption, on ne rattrape pas** : la prochaine échéance repart de
maintenant. Jamais de rafale après une reprise.

| Port | Période initiale | Stocké | Pourquoi |
| --- | --- | --- | --- |
| `wusrd` | **60 s**, puis paliers 30 → 15 s | chaque lecture | cadrage §2.A. Un palier n'est retenu qu'après 24 h sans un `500` ; au premier `500`, retour au palier précédent, journalisé |
| `wusts` | 10 s | au changement | datation de l'alarme, du rappel et de sa fin (§5). Le transitoire `2` (7,46 s) sera vu environ trois fois sur quatre ; il n'est pas un événement |
| `wungt` | 30 s | au changement | figer une session avant la suivante |
| `dataupload/*/data` (×4) | 5 min | au changement | fenêtre de 15 min, publiée avec une fenêtre de retard |
| `wulgt`, `wudsk`, `wualm`, `wualm/aenvs`, `wualm/aalms`, `wuply` | 60 s | au changement | le miroir que sert l'API, et l'instantané des réglages |
| Détail des 16 profils (`wualm/prfwu`) | 1 fois par jour, et après tout changement de `aenvs`/`aalms` | au changement | instantané complet, pour restaurer un réveil réinitialisé |
| `products/0/time`, `wutim`, `wutms` | 1 h | `clock_check` | la dérive (§6) |
| `backend`, `transport`, `device` | 1 h | au changement | l'isolement, vérifiable dans la durée |
| `files/*` | au démarrage, puis 1 fois par jour | au changement | les noms des thèmes et des sons |

Charge totale : ~16 requêtes par minute, en dessous du régime mesuré le 2026-09-07 (24 par
minute, 45 min, zéro échec). Sur une connexion réutilisée, une lecture coûte
~36 ms : l'appareil passe plus de 95 % du temps sans aucune requête.

**Lire un profil d'alarme passe par une écriture.** `pysomneo` sélectionne le profil par
`PUT wualm {"prfnr": n}`, puis le relit. C'est une écriture sans effet observable — le
profil ne change pas — mais c'en est une, et sur les alarmes. **Tranché le 2026-09-12** : une
fois par jour, en journée, jamais pendant une alarme, et après tout changement vu dans
`aenvs`/`aalms` — une fois l'essai P3 passé (§1).

**Tant que SleepMapper est installée**, chaque ouverture de l'app ouvre une seconde connexion
et fait tomber des lectures du collecteur. Ce n'est pas une panne : c'est la cause « appareil
saturé », et elle disparaîtra avec la désinstallation.

---

## 5. Les nuits

Une machine à états par session. Ses règles viennent des quatre matins d'alarme mesurés
(07 → 10/09) et de la nuit sans appui du 6 au 7 — `docs/somneo-api.md` §4.

| Transition | Déclencheur | Ce qui est retenu |
| --- | --- | --- |
| → `pending_device` | appui « je me couche » reçu, réveil injoignable | l'heure de l'appui, **confirmée** |
| → `open` | notre écriture relue dans `wungt` (`night: true`), ou changement de `wungt` observé sans nous (SleepMapper) | coucher = heure de l'appui, ou `tg2bd` **vu changer** — jamais un `tg2bd` lu à froid : le champ est périmable |
| `open` → `closed` | appui « je me lève » | lever = heure de l'appui, **confirmé** |
| `open` → `closed` | `wungt` repasse à `night: false` sans nous, **et** une alarme a sonné (bit 11 levé puis retombé dans `wusts`) | lever = la **fin de l'alarme**, première lecture où le bit 11 est retombé, **estimé**. `tendb` est conservé brut, jamais utilisé : il vaut l'heure programmée |
| `open` → `abnormal` | `wungt` repasse à `night: false` sans alarme — l'expiration à 12 h | pas d'heure de lever. Signalée, jamais close en silence |
| `open` → `abnormal` + nouvelle `open` | nouvel appui de coucher, session ouverte | la précédente est figée d'abord, marquée close par nécessité |
| — | double appui | rien : la seconde demande renvoie la session en cours |

Trois points de conception en découlent :

- **C'est le firmware qui clôt la nuit, à l'heure programmée de l'alarme**, avant tout geste
  (08, 09 et 10/09). Le collecteur ne peut donc pas attendre un geste pour fermer une nuit. Il
  attend la fin de l'alarme dans `wusts`, qui arrive une à quinze minutes plus tard.
- **La sortie d'alarme n'a pas de forme fixe** (`2817 → 2 → 1`, `2817 → 258 → 257 → 2 → 1`,
  `2817 → 1`). Seule la chute du bit 11 est commune aux quatre matins. Aucune séquence n'est
  attendue.
- **Une lecture peut échouer au moment exact de l'appui** (le 10/09). La fin d'alarme est alors
  bornée par les deux lectures qui l'encadrent. L'incertitude est stockée, pas effacée.

**Corrections.** Une correction s'ajoute dans `night_correction`, la valeur relevée reste. Un
lever antérieur au coucher est refusé, avec la raison. La dernière correction fait foi. Une
correction ne produit jamais « estimé » : elle produit « corrigé ».

**Rattachement.** Une nuit appartient au jour local (Europe/Paris) de son heure de coucher.

---

## 6. L'horloge du réveil

- **Chaque heure, le collecteur mesure** : heure de la carte, `products/0/time`, `wutim`, et
  l'écart, au point milieu de la requête. **Chaque contrôle est journalisé**, pas seulement
  ceux qui précèdent une correction : c'est la courbe de dérive du réveil isolé, la seule
  mesure qu'on en aura.
- **Une fois par jour, en journée** (12 h – 18 h), il corrige si l'écart dépasse **10 s**
  (tranché le 2026-09-12), et seulement si :
  - l'horloge de la carte est synchronisée (`timedatectl` : `NTPSynchronized=yes`) — sinon il
    ne corrige rien et le signale ;
  - aucune nuit n'est ouverte ;
  - `wusts` ne montre ni alarme, ni rappel (bits 2, 4 et 11 à zéro).
- **La bascule du 25 octobre 2026.** Autour de 3 h, le collecteur relit `time`, `wutms` et
  `wutim` **toutes les minutes** de 1 h à 5 h, heure locale. Cette instrumentation est en
  lecture seule et n'attend pas P2 : elle doit être **en service avant le 25 octobre**. Si
  l'isolement n'est pas fait d'ici là, on observera un réveil encore relié au cloud, ce qui
  reste utile : c'est la référence à laquelle comparer le printemps suivant.
- **Le code de correction attend P2** (§1).

---

## 7. L'API

Versionnée (`/v1`), en JSON, sans authentification, sur le seul réseau domestique. Chaque
réponse porte `served_at` ; tout ce qui vient du miroir de l'appareil porte `observed_at`. C'est
ce qui permet à l'app de s'ouvrir sans attendre le réveil, en disant de quand date ce qu'elle
montre.

### Lecture

| Route | Sert |
| --- | --- |
| `GET /v1/status` | liaison au réveil (et depuis quand), dernier relevé réussi, trous récents et leur cause, écart d'horloge, liaison cloud (`dcs-state`, `lastsignon`, `transport.state`, `allowuploads`), alarme masquée mais armée, état du disque, palier de cadence |
| `GET /v1/nights?from=&to=` | les nuits d'une période, avec leurs résumés (min, moyenne, max par grandeur, couverture). Un mois tient en une réponse |
| `GET /v1/nights/{id}` | une nuit, ses corrections, sa valeur relevée d'origine |
| `GET /v1/readings?from=&to=` | les points bruts |
| `GET /v1/aggregates?from=&to=` | les agrégats de fenêtre |
| `GET /v1/outages?from=&to=` | les périodes d'indisponibilité, nommées |
| `GET /v1/device` | le miroir : alarmes visibles, lumière, veilleuse, coucher de soleil, rappel, `wusts` décodé en bits |
| `GET /v1/catalog/themes` | numéros → noms des thèmes et des sons, avec leur source (appareil ou relevé de SleepMapper) |
| `GET /v1/sync` | le rattrapage, ci-dessous |

Les résumés par nuit sont **calculés à la demande**, jamais stockés : ils ne peuvent pas
diverger de leur série.

### Écriture — le relais

| Route | Effet |
| --- | --- |
| `POST /v1/nights/bedtime` | geste de coucher. `201` si le réveil l'a pris, `202` « en attente du réveil » sinon |
| `POST /v1/nights/risetime` | geste de lever |
| `POST /v1/nights/{id}/corrections` | correction d'heure — la seule écriture de l'app dans la mémoire |
| `PUT /v1/light`, `PUT /v1/nightlight` | lumière, veilleuse |
| `PUT /v1/sunset` | coucher de soleil : marche, arrêt, réglages |
| `POST /v1/alarms`, `PUT /v1/alarms/{n}`, `DELETE /v1/alarms/{n}` | alarmes. Seize emplacements occupés : `409`, jamais d'écrasement |
| `PUT /v1/snooze` | durée du rappel, globale |

**Chaque écriture suit la même forme** : bornes vérifiées avant l'envoi (celles du cadrage
§3.D, qui ne s'uniformisent pas), passage par la file de la passerelle, **relecture du port**,
puis réponse avec l'état relu. Si la relecture ne montre pas la valeur, c'est un échec, même
après un `200` : l'appareil fait foi, pas sa réponse.

`pysomneo` couvre l'heure, les jours, le thème, l'intensité, la durée, la source, la piste, le
volume, le PowerWake et la durée du rappel. **Pas le départ en douceur** : `set_alarm_sound`
n'écrit pas `sndss`, dont la seule occurrence dans `somneo.py` est une constante à 0 ailleurs
(vérifié sur `365d313`, le 2026-09-12). Il s'écrira en direct sur `wualm/prfwu`, par la même
passerelle.

### Le rattrapage — contrat arrêté le 2026-09-12

Le cadrage dit « tout ce qui est arrivé **depuis telle date** ». Une date ne suffit pas : une
nuit vieille d'une semaine, corrigée ce matin, n'est pas « arrivée depuis » la dernière
synchronisation — et le cadrage exige pourtant qu'elle revienne corrigée (§3.F).

En deux temps :

1. **Première synchronisation, les nuits récentes d'abord** : `GET /v1/sync?before=<jour>`
   renvoie les N nuits qui précèdent ce jour, avec leurs points et leurs agrégats, du plus
   récent au plus ancien. Le client recule page par page jusqu'à une réponse vide. La première
   réponse lui donne aussi la **séquence courante**.
2. **Ensuite, par séquence** : `GET /v1/sync?since_seq=<n>` renvoie tout ce qui a été créé **ou
   modifié** depuis, dans l'ordre. Une correction faite pendant la première synchronisation
   porte une séquence plus récente : elle revient d'office.

Le collecteur ne tient aucun état du client. Une même demande rend le même contenu. **C'est un
changement du contrat**, validé le 2026-09-12 et reporté dans les deux cadrages (§11).

---

## 8. Déploiement

- **Sur la carte** : code et venv dans `/opt/somneo-collector`, base et sauvegardes dans
  `/var/lib/somneo-collector`, un utilisateur système dédié. Journal par journald.
- **L'unité systemd** : `Restart=always`, `After=network-online.target` **sans** `Requires=` —
  le cadrage veut que le collecteur démarre même sans réseau ni réveil, et cherche en boucle.
- **`pysomneo`** installé depuis le fork à `13ec0c5`, puis depuis PyPI dès la 6.0.0.
- **Développement** : tests hors appareil sur le poste ; déploiement par copie et
  `systemctl restart`. Tout essai contre l'appareil part de la carte.

### La bascule depuis la capture — le piège

**`superviseur.sh` ne verra pas le collecteur.** Il relance `capture.py` dès qu'aucune sonde de
`somneo-dev/` ne tourne ; un service dans `/opt` n'en fait pas partie. Démarrer le collecteur
à côté, c'est deux clients : l'appareil ferait tomber les deux, et on perdrait les deux
relevés à la fois.

Ordre de bascule : arrêter le superviseur **par son PID**, puis la capture **par son PID**
(`SIGTERM`, elle le gère), puis démarrer le collecteur. Retour arrière : l'inverse. Jamais de
`pkill -f` ni de `pgrep -f` distant portant le nom de la cible (`AGENTS.md`).

---

## 9. Tests

- **Hors appareil, rejouables par n'importe qui.** Un faux réveil qui reproduit les deux
  comportements qui comptent : les réponses (`200`, `422`, `500 Timeout`, `400`) et **la
  connexion unique**, qui fait tomber la requête en vol quand une seconde arrive. Le test
  cardinal : sous n'importe quelle charge de l'API, la passerelle n'a jamais deux requêtes en
  vol.
- **Les fixtures ont la forme des réponses réelles, pas leurs valeurs.** Dépôt public : les
  corps de `wusrd` et `dataupload` décrivent la chambre, ceux de `wungt` datent des couchers.
  Valeurs remplacées, heures données en écarts (`C`, `A`, `E`), comme dans `docs/`.
- **La machine à états des nuits**, sur des séquences scriptées depuis les mesures : la nuit
  du 7 au 8, les quatre sorties d'alarme, la lecture échouée à `A + 1 min 04 s`, l'expiration à
  12 h, le « coucher, lever, recoucher », une nuit à cheval sur le 25 octobre 2026 et une sur
  le 28 mars 2027.
- **Les critères d'acceptation du cadrage** (§4) se vérifient sur l'appareil, et certains
  prennent un mois. Ils se cochent dans le cadrage au fil de l'eau.

---

## 10. Ordre de construction

Le risque cardinal est la **nuit perdue**. Aujourd'hui, `capture.py` est le seul filet ; le
premier incrément doit donc le remplacer entièrement, et rien d'autre.

**Avant l'incrément 1 : les préalables P1 à P3** (§1, §11 point 10). P1 décide d'une partie
du schéma des nuits ; P3 conditionne l'instantané des profils.

| Incrément | Contenu | Condition de passage |
| --- | --- | --- |
| **1. Collecte** | passerelle, SSDP, base, planificateur (tous les ports du §4, horloge en lecture seule, instrumentation de la bascule), indisponibilités, sauvegarde, `GET /v1/status` et `/v1/readings` | tourne sept jours sans intervention ; la capture est arrêtée |
| **2. Nuits** | machine à états, en lecture seule (gestes faits par SleepMapper), `/v1/nights`, corrections | une nuit « coucher, lever, recoucher » donne deux sessions |
| **3. Rattrapage et mDNS** | `/v1/sync`, `/v1/catalog/themes`, annonce mDNS | contrat validé côté SleepMaxxer |
| **4. Relais du pilotage** | gestes (après P1), lumière, coucher de soleil, alarmes | chaque écriture relue ; aucune n'a eu d'effet que l'utilisateur n'a pas demandé |
| **5. Remise à l'heure** | correction quotidienne (après P2) | un mois sans dérive au-delà du seuil |

Puis, hors code : l'isolement à la box, **une fois l'historique en train de se constituer**.

**Échéance dure : le 25 octobre 2026.** L'incrément 1 doit tourner avant, pour que la bascule
soit observée à la minute.

---

## 11. À trancher par Victor

Les onze points ont été tranchés par Victor le 2026-09-12, un par un. Les motifs sont dans le
corps du plan ; ce qui est reporté ailleurs est dit au point concerné.

1. ~~Arborescence et noms~~ — **tranché le 2026-09-12, tel que proposé** : `collector/`, paquet
   `somneo_collector`, `/opt/somneo-collector`, `/var/lib/somneo-collector`, utilisateur système
   `somneo`, unité `somneo-collector.service`.
2. ~~Cadences initiales~~ — **tranché le 2026-09-12, telles que proposées** : `wusrd` à 60 s puis
   paliers, `wusts` à 10 s, `wungt` à 30 s, réglages à 60 s, agrégats à 5 min, horloge et
   liaison cloud à 1 h.
3. ~~Stockage au changement~~ — **tranché le 2026-09-12** : au changement pour les ports d'état
   et les agrégats, chaque lecture pour `wusrd` (§3, principe 3).
4. ~~Quatrième cause d'indisponibilité~~ — **tranché le 2026-09-12 : oui**, « carte hors
   réseau », nommée par l'app comme les trois autres (§3).
5. ~~Rattrapage par séquence~~ — **tranché le 2026-09-12 : oui**, en deux temps (§7).
6. ~~Nom du service mDNS~~ — **tranché le 2026-09-12 : `_somneo-scraper._tcp`**, TXT `api=v1`
   (§2).

   Les points 4 à 6 touchent au contrat : reportés le jour même dans les deux cadrages (§5 de
   chacun), dans l'`AGENTS.md` de SleepMaxxer et dans les jumelles des deux projets.
7. ~~Rotation des sauvegardes~~ — **tranché le 2026-09-12** : 7 quotidiennes et 4
   hebdomadaires, compressées, sous un plafond de 1,5 Go (§3).
8. ~~Remise à l'heure~~ — **tranché le 2026-09-12 : seuil de 10 s** (5 s étaient proposés),
   fenêtre 12 h – 18 h (§6).
9. ~~Lecture quotidienne des 16 profils~~ — **tranché le 2026-09-12 : oui**, une fois par jour
   et après tout changement de `aenvs`/`aalms`, une fois l'essai P3 passé (§1, §4).
10. ~~Préalables P1, P2 et P3~~ — **tranché le 2026-09-12 : accord donné** pour écrire sur
    `wungt`, sur `time` et sur `wualm` (sélection d'un profil), **avant l'incrément 1** — et,
    à sa demande, **les trois le soir même**, aucune alarme ne sonnant avant le 14. Victor a vu
    la liste des tests et délégué la relecture du code : elle est remplacée par un essai contre
    un faux réveil local, témoins positifs compris, puis un passage en lecture seule sur
    l'appareil.
11. ~~Heure de lever estimée~~ — **tranché le 2026-09-12** : la première lecture où le bit 11 est
    retombé, la lecture précédente gardée comme borne basse (§5).
