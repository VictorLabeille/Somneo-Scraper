# Cadrage — Somneo-Scraper, le collecteur

> Statut : **validé** · Date : 2026-09-06

Premier cadrage du backend. Il porte sur le **collecteur entier**, pas sur une fonctionnalité.
Il arrive volontairement **après** celui de SleepMaxxer : la forme de l'API interne doit être
dictée par ce dont l'application a besoin, décision du 2 septembre 2026. Ce document ne
réarbitre donc pas ce que l'app fait — il décide de ce que le collecteur doit être pour que
l'app tienne ses promesses.

Références : `docs/somneo-api.md` (protocole du réveil, 21 ports, sémantique des champs),
`docs/radxa.md` (la carte et ses contraintes), et du dépôt SleepMaxxer
`.claude/specs/2026-09-05-spec-fonctionnelle-sleepmaxxer.md` (le cadrage auquel cette API
répond), `docs/seuils-conditions-sommeil.md`, `docs/sleepmapper/README.md`.

---

## 1. Contexte & objectif métier

### Problème / besoin

Le Philips Somneo HF3671/01 expose une API REST locale complète et sans authentification —
mais **sans aucune mémoire**. Elle ne donne que l'instant présent (`wusrd`), la fenêtre
d'agrégation de 15 minutes en cours (`dataupload/*/data`) et la nuit en cours (`wungt`).
Les courbes d'historique de SleepMapper viennent du cloud Philips, que le réveil alimente
lui-même toutes les 15 minutes.

**Couper l'accès internet du réveil détruit donc tout l'historique** — et l'isolement est
l'objectif du projet. Le collecteur n'est pas un cache d'accélération : une fois la coupure
faite, **sa base est la seule mémoire qui existe**. Ce qu'il ne relève pas n'est perdu pour
personne d'autre : c'est perdu tout court, et ne se remesure jamais.

S'y ajoute une seconde dépendance, moins visible : le réveil isolé **n'a plus de source de
temps**. `tmser` vaut `http://www.noserver.com`, `tmsrc` vaut `irq` — son heure venait de la
liaison cloud. Une horloge qui dérive corrompt silencieusement les heures de coucher, et fait
sonner les alarmes à côté. C'est le risque le plus concret de la coupure, et il ne se répare
que depuis un appareil qui tourne en continu : le collecteur.

### Valeur attendue / pourquoi maintenant

- **Rendre l'isolement possible.** Sans mémoire propre, couper internet n'est pas une option.
- **Faire mieux que l'original sur la donnée.** Philips échantillonnait au quart d'heure. Une
  mesure par minute, plus les extrema de chaque fenêtre, capte ce que le cloud manquait.
- **Réparer ce que la coupure casse** : l'heure du réveil, que seul un service continu peut
  tenir à jour.
- **Supprimer l'attente au démarrage de l'app** : elle lit un historique déjà constitué au
  lieu d'attendre un appareil lent.
- **Servir de pièce de portfolio** — candidature de stage (mai–novembre 2027) auprès d'un
  chercheur en e-health du département de biomédecine de LTH, à Lund. Le collecteur est la
  partie invisible mais décisive d'une chaîne d'acquisition construite de bout en bout :
  capteurs → collecte → historisation → restitution.

### Parties prenantes & utilisateurs

**Un seul utilisateur, un seul réveil, un seul téléphone, aucun rôle.** Le collecteur n'a
qu'un client : SleepMaxxer. Il n'a pas d'interface humaine.

Un second public, non-utilisateur : les lecteurs du portfolio. Ils ne s'en serviront jamais ;
ils jugeront la méthode et la cohérence du récit. Ce public justifie l'exigence de rigueur —
**jamais** un ajout de fonctionnalité.

### Indicateurs de succès

- **Aucune nuit perdue depuis l'isolement.** C'est l'indicateur cardinal : la donnée ne se
  remesure pas.
- **Le collecteur tourne des semaines sans intervention.** S'il faut ouvrir une session SSH
  pour le remettre en route, il a échoué — la carte est fixée au dos du réveil, dans une
  chambre, sans console série.
- **La courbe d'une nuit est plus fine que ce que Philips produisait**, et les trous y sont
  visibles au lieu d'être comblés.
- **L'app s'ouvre sur une information utile sans attendre le réveil.**
- **Aucune écriture du collecteur n'a jamais eu d'effet observable dans la chambre.** Ni
  lumière, ni son, ni alarme déplacée.

### Contrainte cardinale de périmètre

Héritée du cadrage de SleepMaxxer et maintenue ici : **une fonction n'entre que si elle sert
un geste réel ou une exigence écrite du cadrage de l'app.** Deux échecs documentés la
justifient (l'onglet Stats de Commit & Push, livré et jamais ouvert ; FshnRps-GAOE, trop lourd
pour son usage). Le collecteur a une tentation propre et bien à lui : l'API du réveil offre
beaucoup plus que ce que l'app demande, et tout y est facile à ajouter.

**Une seule exception, nommée et bornée** : ce qui relève de la **conservation d'une donnée
qui ne se remesure pas** peut être enregistré sans être exposé (§2, journal d'événements).
Enregistrer coûte peu et se rattrape jamais ; exposer crée une surface.

---

## 2. Périmètre

### Dans le périmètre

#### A. Collecte des capteurs

- **En continu, 24 h/24**, jamais seulement pendant les nuits. Le réveil n'ayant aucune
  mémoire, une période non relevée est perdue définitivement — et on ne sait qu'après coup
  qu'une nuit a commencé : ne collecter que pendant les sessions déclarées perdrait le début
  de toute nuit dont le geste a été oublié.
- **Une mesure par minute au minimum** — 1 440 points par jour et par grandeur, contre 96 pour
  le cloud Philips. Température, humidité, lumière et bruit, lus dans `wusrd`.
- **Plus fin si l'appareil le supporte.** La cadence se monte **par paliers** (1 min → 30 s →
  15 s), et un palier n'est retenu que si **24 heures s'écoulent sans un seul `500 Timeout`**.
  Critère mesurable, qui ne dépend d'aucune impression. La cadence est donc un réglage, et
  **rien dans la conception ne doit présumer d'un pas de temps fixe**.
- **Les extrema et histogrammes de chaque fenêtre de 15 minutes**, lus dans
  `dataupload/{temp,hum,snd,lux}.1/data` : minimum, maximum, moyenne, et pour le bruit et la
  lumière les histogrammes de valeurs absolues et de variations. C'est ce qui capte **le pic
  survenu entre deux mesures** — un bruit à 70 dB entre deux relevés est absent de la série,
  mais reste dans `hisnd`. Sans cela, le projet serait moins bon que Philips sur les extrêmes
  tout en étant meilleur sur la moyenne.
- **Deux séries distinctes, jamais fondues** : les points mesurés d'un côté, les agrégats de
  fenêtre de l'autre. Une valeur mesurée et une valeur déduite ne se rangent pas ensemble.

#### B. Suivi des nuits

- **Ouverture d'une session** par le geste « je me couche » de l'application, relayé en
  écriture dans le réveil, qui reste le dépositaire de l'heure.
- **Clôture** par le geste « je me lève », et à défaut par ce que le réveil expose dans
  `tendb`. Le collecteur retient **laquelle des deux origines** a produit chaque heure : c'est
  ce qui distingue « confirmé » d'« estimé », et l'app ne fait que restituer l'étiquette.
- **Figer une session terminée avant qu'une suivante ne commence.** `wungt` ne décrit que la
  session en cours : un second coucher dans la même nuit écrase le premier. Sans ce figement,
  le cas « coucher, lever, recoucher » du cadrage de l'app est irréalisable — non par choix
  d'interface, mais parce que la donnée n'existe plus. C'est la cadence de relevé de `wungt`
  qui décide de ce qui est perdu.
- **Jamais d'heure inventée.** Si ni le geste ni le réveil ne fournissent d'heure, la nuit
  existe **sans** heure de coucher, corrigeable à la main. Le collecteur ne déduit pas une
  mise au lit de la lumière ou du bruit : ce serait un jugement dont la règle aurait été
  inventée ici, invérifiable et non citable.
- **Correction manuelle des heures d'une nuit passée** — voir §5, c'est la seule écriture que
  le collecteur accepte de l'application.

#### C. Restitution à l'application

L'API sert, sans jamais juger :

- **Les nuits** — heures, durée, origine de chaque heure (confirmée / estimée / corrigée),
  état (en cours, close, anormale).
- **Les mesures d'une période** — les points bruts *et* les agrégats de fenêtre.
- **Des résumés par nuit** — minimum, moyenne, maximum par grandeur, et durée. Toujours
  **dérivés**, jamais saisis : ils se recalculent, donc ils ne peuvent pas diverger de leur
  série. Ils évitent à l'app de parcourir 1 440 points par grandeur pour afficher un écran, et
  permettent à un mois de calendrier de tenir en une réponse.
- **Le rattrapage** : « tout ce qui est arrivé depuis telle date », **paginé**, les nuits
  **récentes d'abord** — c'est ce qu'on regarde. Reprenable là où il s'est arrêté.
- **L'état du système** : liaison au réveil, dernier relevé réussi, trous récents et leur
  cause, écart d'horloge constaté, état de la liaison cloud du réveil.
- **La correspondance entre les numéros du réveil et les noms lisibles** des thèmes lumineux
  et des sons — lue de l'appareil (`files/*`) et complétée par le relevé de SleepMapper, avec
  sa source citée. Ces noms ne sont publiés par aucune source : le relevé est leur seule trace,
  comme les seuils.

**Le collecteur sert des mesures, jamais des verdicts.** Les seuils de qualité du sommeil
restent figés dans l'application, et c'est elle qui compose les phrases de verdict et le
résumé pour le coach — sans quoi rien de tout cela ne fonctionnerait hors du domicile, là où
la copie locale est justement censée tout permettre en lecture.

#### D. Relais du pilotage

Le collecteur relaie vers le réveil ce que les maquettes de SleepMaxxer demandent, et rien de
plus : alarmes (les profils que le réveil déclare visibles), lumière et veilleuse, coucher de
soleil, geste de coucher et de lever.

- **Il répond de son miroir**, tenu à jour en fond, et **date ce qu'il sert**. C'est ce qui
  permet à l'app de s'ouvrir sans attendre l'appareil.
- **Après chaque écriture, il relit le port avant de confirmer.** Jamais d'écriture annoncée
  comme appliquée sans l'avoir revue dans l'appareil.
- **En cas de désaccord entre le miroir et l'appareil, l'appareil gagne**, toujours.
- **Toutes les commandes sont sérialisées** et espacées d'environ 200 ms. Une rafale de l'app
  ne doit jamais devenir une rafale sur le réveil.

#### E. Tenue de l'heure du réveil

- **Une fois par jour, en journée**, le collecteur compare l'heure du réveil à la sienne et la
  corrige si l'écart dépasse quelques secondes.
- **Il pose l'heure, le fuseau et la bascule saisonnière.** La bascule est le seul moment où
  l'horloge saute d'une heure d'un coup au lieu de dériver : ne poser que l'heure exposerait à
  un réveil qui sonne une heure à côté deux fois par an — la panne la plus visible que ce
  projet puisse produire. **Prochaine bascule : 25 octobre 2026.**
- **Jamais pendant une nuit déclarée, jamais pendant qu'une alarme sonne ou est en rappel.**
- L'application ne remet jamais l'heure ; elle signale un écart si le collecteur en rapporte un.

#### F. Mémoire et durabilité

- **Tout est conservé, indéfiniment.** Pas de fenêtre glissante, pas de purge, pas de
  ré-agrégation des données anciennes. À la cadence de la minute, l'ordre de grandeur est de
  quelques dizaines de méga-octets par an sur un rootfs de 6,9 Go : le problème ne se pose pas
  avant des décennies. Une fenêtre glissante contredirait frontalement la copie locale du
  téléphone — il garderait ce que le collecteur aurait jeté, et « le collecteur fait autorité »
  deviendrait faux.
- **Copie de sauvegarde datée sur la carte, en rotation.** Elle ne protège pas de la mort de
  l'eMMC — c'est le rôle du téléphone et de l'export Drive — mais elle protège du mode de
  panne le plus probable au quotidien : une base corrompue par une coupure de courant en pleine
  écriture, sur une carte alimentée par le port USB d'un réveil.
- **Journal des périodes d'indisponibilité, avec leur cause** : collecteur arrêté, réveil
  injoignable, appareil saturé. Une absence de lignes ne dit pas laquelle des trois s'est
  produite, et l'app doit pouvoir montrer un trou **en le nommant** plutôt qu'en le laissant
  ressembler à une base vide.
- **Instantané daté des réglages du réveil à chaque changement constaté** — alarmes (les seize
  profils, pas seulement les visibles), thèmes, coucher de soleil, réglages globaux. Il
  alimente l'export du téléphone, dont c'est la raison d'être : remettre en état un réveil
  réinitialisé ou remplacé. L'historiser permet en plus de voir ce qui a changé et de revenir
  en arrière, sans dépendre d'une synchronisation récente du téléphone.
- **Journal des événements du réveil** — alarme déclenchée, rappel, lumière allumée, coucher
  de soleil lancé. Enregistrés parce qu'ils ne se remesurent pas ; **non exposés par l'API**
  tant qu'aucun besoin ne s'est manifesté. C'est l'unique exception à la règle cardinale, et
  elle est bornée : enregistrer ne crée aucune surface.
- **Un changement d'appareil est marqué, la série reste continue.** L'historique est celui de
  la chambre, pas celui d'un numéro de série — mais la rupture est datée, pour qu'elle puisse
  expliquer une discontinuité dans les mesures.

#### G. Découverte et exposition

- **Le collecteur découvre le réveil par SSDP**, jamais par une adresse en dur, et
  **redécouvre périodiquement** — l'adresse est en DHCP.
- **Le collecteur s'annonce lui-même sur le réseau** (mDNS), pour que l'application le trouve
  seule à la première ouverture et le retrouve quand son propre bail change. Aucune adresse en
  dur nulle part, ni dans un sens ni dans l'autre. C'est la réponse à une question laissée
  ouverte par le cadrage de SleepMaxxer.
- **Aucune authentification**, sur le seul réseau domestique. L'app n'aggrave aucun risque
  existant : le réveil livre déjà sa clé de sécurité et son reset d'usine à quiconque est sur
  le LAN. Hypothèse à ré-examiner si le réseau domestique cesse d'être de confiance.
- **Le collecteur observe la liaison cloud du réveil** (`backend`, `transport`,
  `device.allowuploads`) et rapporte si l'appareil est encore relié à Philips. C'est le moyen
  simple de vérifier que l'isolement tient dans la durée.

### Hors périmètre (explicite)

| Exclu | Pourquoi |
| --- | --- |
| **Le blocage internet lui-même** | Se fait à la box (règle, VLAN ou DNS sinkhole) : c'est une opération réseau, pas du code. Le collecteur le **vérifie**, il ne le réalise pas. |
| **Couper la liaison cloud depuis l'appareil** (`allowuploads`, `dcsenabled`) | Examiné et écarté, voir §5. Ces interrupteurs dépendent du firmware : une mise à jour ou un reset d'usine peut les rétablir en silence, et le réveil garderait sa route vers internet. |
| **Toute interface web**, même une page d'état | Le contrôle se fait par l'API et en SSH. Une page de consultation serait SleepMaxxer en double, dans un projet dont la règle cardinale l'interdit. |
| **Exposition hors du réseau domestique** (VPN, tunnel, ouverture de port) | Le besoin qui la motivait — consulter hors du domicile — est supprimé par la copie locale du téléphone, pas contourné. |
| **Écriture en masse depuis le téléphone** | Le téléphone ne repeuple jamais un collecteur neuf ; celui-ci se remplit à la main depuis le Drive. Le backend n'a donc aucun point d'entrée qui recevrait des données non vérifiées. |
| **Verdicts, seuils, phrases de qualité, résumé coach** | Composés par l'application, à partir de seuils figés et sourcés. Le collecteur ne juge pas ce qu'il mesure. |
| **RelaxBreathe, radio FM, réglages d'afficheur, minuteurs** | Hors périmètre de l'application. Les réglages d'afficheur restent de la matière de contribution `pysomneo` (issue #13), pas une fonction d'ici. |
| **Abonnement UDP** | À **essayer** avant d'en dépendre (voir §6). La collecte se bâtit sur l'interrogation, qui est éprouvée. |
| **Multi-utilisateur, multi-téléphone, notifications poussées** | Aucun usage identifié. |
| **Base de données séries temporelles dédiée** | SQLite suffit à un historique personnel ; pas d'Influx sans besoin démontré. |

### Hypothèses

- Le collecteur tourne sur la **Radxa Zero en service**, alimentée par le port USB du réveil,
  et **redémarre seul** après une coupure de courant, sans intervention.
- **La Radxa, elle, garde son accès internet.** L'isolement ne vise que le réveil. C'est ce qui
  donne au collecteur une heure juste à poser dans l'appareil — sans quoi la fonction n'a
  aucun sens.
- Le collecteur est le **seul client** du réveil une fois SleepMapper désinstallée.
- Le réveil **n'exige aucune authentification**, ni en lecture ni en écriture (vérifié le
  31 août 2026).
- `pysomneo` couvre 11 des 21 ports ; les autres se traitent par appels directs **à côté** de
  la bibliothèque, sans la remplacer.
- Une **nuit** est rattachée au jour de son heure de coucher, en heure locale.
- L'appareil est **en service dans une chambre** : une écriture à la fois, choisie sans effet
  observable, jamais sur les alarmes ou la lumière le soir.

---

## 3. Cas limites, erreurs & états dégradés

### A. Liaison au réveil

| Cas | Déclencheur | Comportement attendu |
| --- | --- | --- |
| Réveil injoignable | Débranché, redémarrage, adresse changée | Réessai **espacé progressivement**, et **redécouverte SSDP** au bout d'un moment : la cause la plus probable n'est pas la panne mais un bail DHCP qui a changé. L'API continue de répondre et de servir tout l'historique. |
| Réveil saturé | `500 Timeout` sous une rafale — comportement matériel, ~25 ko de tas libre | Ce n'est pas une panne. On ralentit, on relance, on ne compte pas cela comme une indisponibilité tant que ça reste isolé. Jamais de rafale de rattrapage après une reprise. |
| Adresse changée | Nouveau bail DHCP | Redécouverte SSDP, reprise sans intervention. Aucune adresse n'est jamais mémorisée comme définitive. |
| Découverte impossible au démarrage | La carte démarre avant le réveil, ou le WiFi n'est pas encore là | Le collecteur démarre quand même, sert l'historique, et cherche le réveil en boucle. Il ne s'arrête jamais faute d'appareil. |
| Réveil réinitialisé ou remplacé | Reset d'usine, changement de matériel | La rupture est **datée et marquée**, la série de mesures reste continue. Les réglages sont restaurables depuis le dernier instantané. |
| Liaison cloud rétablie | Une règle de box a sauté | Le collecteur le constate en relisant `backend` / `transport` et le rapporte. Il ne tente rien pour la recouper. |

### B. Collecte et trous

| Cas | Déclencheur | Comportement attendu |
| --- | --- | --- |
| Collecteur arrêté | Redémarrage, coupure de courant, mise à jour | Une **période d'indisponibilité nommée** est enregistrée, bornée par le dernier relevé réussi et le premier de la reprise. Le trou est visible et sa cause lisible. |
| Réveil muet pendant une période | Panne ou débranchement prolongé | Même chose, avec une cause différente : l'app doit pouvoir dire laquelle. |
| Mesure aberrante | Valeur hors de toute plage physique | **Enregistrée telle quelle.** Le collecteur n'est pas juge de la réalité ; il n'invente ni ne masque une mesure. |
| Grandeur absente | Un capteur ne renvoie rien alors que les autres répondent | Cette grandeur seule est absente pour cet instant ; les autres sont enregistrées. Pas d'échec global du cycle. |
| Fenêtre d'agrégation manquée | Le collecteur n'a pas lu `dataupload/*/data` avant que la fenêtre bascule | Les extrema de cette fenêtre sont perdus, la période est marquée comme telle. **Jamais reconstitués depuis les points**, qui ne les contiennent pas. |
| Palier de cadence trop ambitieux | Des `500 Timeout` apparaissent après une montée | Retour automatique au palier précédent, et l'événement est journalisé. On ne laisse pas une cadence dégrader la collecte pour gagner en finesse. |
| Base volumineuse | Des années de mesures | Aucun traitement particulier. Le volume attendu ne justifie ni purge ni ré-agrégation ; si la situation change, ce sera une nouvelle décision, pas un comportement silencieux. |
| Disque plein | eMMC saturée | La collecte s'arrête **proprement** et le dit dans l'état du système. Jamais d'écriture partielle, jamais de base corrompue pour gagner un jour de mesures. |

### C. Nuits

| Cas | Déclencheur | Comportement attendu |
| --- | --- | --- |
| Appui « je me couche » alors que le réveil ne répond pas | Réveil débranché, carte qui redémarre, adresse changée | **L'heure de l'appui est conservée et posée dans le réveil dès qu'il répond.** Le geste n'est jamais perdu. L'app affiche « en attente du réveil », **jamais** « suivi en cours » : ce qui n'est pas encore vrai ne s'affiche pas comme vrai. Voir §5. |
| Double appui | Deux pressions rapprochées | Une seule session. La seconde ne crée rien et ne déplace pas l'heure. |
| Appui après-coup | Appui à 2 h pour un coucher à 23 h | L'heure enregistrée est celle de l'appui, marquée **confirmée**, et corrigeable ensuite. |
| Geste de lever absent | On ne marque pas le lever | La session se clôt sur `tendb` si le réveil le fournit, et l'heure est alors marquée **estimée**. Sinon elle reste ouverte. |
| Nuit jamais close | Ni geste, ni `tendb`, et le temps passe | La session **reste ouverte et visiblement anormale**. Aucune clôture automatique silencieuse. |
| Nouveau coucher alors qu'une session est ouverte | Coucher, lever non marqué, recoucher | La session précédente est **figée avant** que la nouvelle ne commence, marquée close par nécessité et signalée comme anormale. Deux sessions distinctes existent, aucune n'écrase l'autre. |
| Sieste, nuit très courte | Session d'une heure | Enregistrée telle quelle. Le collecteur ne décide pas de ce qui est une « vraie » nuit. |
| Nuit à cheval sur minuit | Cas normal | Rattachée au jour de l'heure de coucher. Une nuit ne se scinde jamais. |
| Correction incohérente | Lever antérieur au coucher | **Refusée**, valeur précédente conservée, raison renvoyée à l'app. |
| Nuit corrigée puis re-corrigée | Plusieurs corrections successives | La dernière correction fait foi ; la **valeur relevée d'origine reste conservée** et n'est jamais écrasée. |
| Nuit traversant la bascule saisonnière | 25 octobre 2026, puis chaque printemps et automne | La durée reste juste. Aucune nuit fantôme, aucune nuit dupliquée. |

### D. Pilotage et écritures

| Cas | Déclencheur | Comportement attendu |
| --- | --- | --- |
| Écriture refusée par le réveil | Le réveil renvoie une erreur, ou la relecture ne montre pas la valeur | **Échec explicite**, ancienne valeur conservée dans le miroir. Jamais d'affichage optimiste : sur une alarme, l'illusion se paie au réveil. |
| Écriture acceptée mais non reflétée | Le réveil répond 200, la relecture montre autre chose | Traité comme un échec. **L'appareil fait foi, pas sa réponse.** |
| Deux commandes rapprochées | L'app envoie deux actions coup sur coup | Sérialisées et espacées. Le collecteur n'envoie jamais deux requêtes en parallèle, quelle que soit la charge côté app. |
| Réveil piloté par sa façade | On change la lumière au bouton | Le miroir se corrige au cycle suivant. **En cas de désaccord, l'appareil gagne.** |
| Plus d'emplacement d'alarme | Les seize profils sont occupés | Limite atteinte signalée explicitement. **Jamais d'écrasement silencieux** d'un profil existant. |
| Alarme masquée mais armée | Un profil non visible reste activé | Anomalie : **aucune alarme masquée ne doit pouvoir sonner**. Signalée dans l'état du système. |
| Écriture pendant une alarme | Le réveil sonne ou est en rappel | Le collecteur n'écrit rien de sa propre initiative. Les commandes de l'utilisateur passent. |
| Valeur hors bornes | Durée, intensité, volume hors des bornes relevées | Refusée avant l'envoi. Les bornes diffèrent selon l'écran (lever 5–40 min, coucher 5–60 min, intensité 1–25 au lever et 0–25 au coucher) et **ne s'uniformisent pas**. |

### E. Heure et fuseau

| Cas | Déclencheur | Comportement attendu |
| --- | --- | --- |
| Dérive constatée | Écart de quelques secondes ou plus | Corrigée à la fenêtre de correction quotidienne, en journée. L'écart constaté est journalisé **avant** correction — c'est la seule mesure qu'on aura de la dérive réelle du réveil isolé. |
| Écart important | Plusieurs minutes | Corrigé de la même façon, mais **jamais** pendant une nuit déclarée ni une alarme : une correction d'horloge pendant une nuit fausserait ses heures, et pendant une alarme pourrait la faire sauter. |
| Bascule saisonnière | 25 octobre 2026, puis deux fois par an | Le collecteur pose heure, fuseau et bascule. Ce que fait le réveil isolé **seul** ce jour-là n'a jamais été observé : à instrumenter d'ici là (§6). |
| Heure du collecteur fausse | La Radxa a perdu son heure réseau | **Il ne corrige rien.** Poser une heure fausse dans le seul appareil qui fait autorité serait pire que la dérive. L'anomalie est signalée. |
| Le réveil refuse l'écriture de l'heure | Erreur ou non-application | Échec journalisé et resignalé, pas de nouvelle tentative immédiate. |

### F. Application et rattrapage

| Cas | Déclencheur | Comportement attendu |
| --- | --- | --- |
| Première synchronisation | App neuve devant des mois d'historique | Servi **par lots paginés, les nuits récentes d'abord**. Le collecteur reste utilisable pendant ce temps. |
| Rattrapage interrompu | App fermée, réseau coupé, téléphone en veille | Reprend là où il s'était arrêté. Le collecteur ne tient aucun état de progression du client : c'est le client qui redemande depuis sa dernière date. |
| Période déjà connue redemandée | Le téléphone redemande ce qu'il a | Servie à l'identique. Les réponses sont **rejouables** : une même demande donne le même contenu. |
| Le téléphone en sait plus que le collecteur | Collecteur réinstallé ou restauré depuis une sauvegarde plus ancienne | Le collecteur ne le sait pas et n'a rien à faire : il sert ce qu'il a. **C'est le téléphone qui ne perd rien** et signale l'écart. |
| Correction arrivée après copie | Une heure est corrigée après que le téléphone a copié la nuit | La nuit revient corrigée au rattrapage suivant et remplace la copie. |
| Nuit en cours demandée | Consultation avant le lever | Servie **marquée en cours**, sans heure de lever, rien de présenté comme définitif. |
| Demande avant le début de l'historique | L'app remonte au-delà du premier relevé | Réponse vide explicite, jamais une erreur. |
| Deux clients simultanés | Cas non prévu mais possible | Les lectures sont servies normalement ; les écritures restent sérialisées vers le réveil, dans leur ordre d'arrivée. |

---

## 4. Critères d'acceptation

- [ ] Le collecteur tourne **trente jours d'affilée** sans intervention SSH, redémarrages de la
      carte compris.
- [ ] La courbe d'une nuit compte **au moins 1 440 points** par grandeur, et les trous y sont
      visibles avec leur cause.
- [ ] Un pic de bruit bref survenu entre deux mesures **apparaît** dans les extrema de sa
      fenêtre.
- [ ] Le réveil est débranché une heure : l'API répond, tout l'historique reste servi, et la
      période d'indisponibilité est nommée.
- [ ] L'adresse du réveil change : la collecte reprend seule, sans intervention.
- [ ] Une nuit « coucher, lever, recoucher » produit **deux sessions distinctes**, toutes deux
      consultables.
- [ ] Une correction d'heure sur une nuit vieille de trois jours est acceptée, et la valeur
      relevée d'origine reste consultable.
- [ ] Un appui « je me couche » fait pendant que le réveil est injoignable **finit par être
      posé dans le réveil**, avec l'heure de l'appui.
- [ ] L'horloge du réveil ne dérive jamais de plus de quelques secondes sur un mois, et l'écart
      relevé avant chaque correction est journalisé.
- [ ] Aucune écriture du collecteur n'a produit d'effet observable dans la chambre.
- [ ] L'app trouve le collecteur **sans qu'on lui saisisse d'adresse**.
- [ ] Un mois de calendrier s'obtient en une seule réponse.
- [ ] La base est effacée puis restaurée depuis la sauvegarde locale sans perte.
- [ ] Le palier de cadence retenu a tenu **24 h sans un seul `500 Timeout`**.

---

## 5. Décisions structurantes et renversements

### La correction d'une nuit passée ouvre la seule porte d'écriture

**Contradiction relevée dans le cadrage de SleepMaxxer.** Il pose « le téléphone n'écrit jamais
dans la mémoire du collecteur », **sans exception**, et prévoit par ailleurs de corriger les
heures de n'importe quelle nuit. Or `wungt` ne tient que la session en cours : corriger une
nuit d'avant-hier via le réveil est **impossible**. Les deux règles ne peuvent pas être vraies
en même temps.

**Décision retenue** : le collecteur accepte la correction. Il enregistre la valeur corrigée
**à côté** de la valeur relevée, sans jamais l'écraser, et sert les deux. La règle devient
« le téléphone n'écrit jamais dans la mémoire, **sauf une correction d'heure, tracée et
réversible** ». Une seule porte, étroite et nommée — ce qui vaut mieux qu'une règle absolue
qu'on enfreindrait en silence.

> **À reporter dans le cadrage et l'`AGENTS.md` de SleepMaxxer** : la règle sans exception
> devient une règle à une exception.

### Un appui « je me couche » n'est jamais perdu

**Question posée** : que faire quand le geste est fait alors que le réveil ne répond pas ?

**Décision retenue** : le collecteur **retient l'heure de l'appui et la pose dans le réveil dès
qu'il répond**. Le geste est la seule chose que l'utilisateur produit lui-même ; le perdre
parce qu'une carte redémarrait serait le pire échec possible sur la fonction la plus simple.

**Ce que cela impose** : l'application affiche « coucher enregistré, en attente du réveil », et
**jamais** « suivi en cours ». La règle « aucun affichage optimiste » n'est pas levée — elle
est appliquée : ce qui n'est pas encore vrai s'affiche comme pas encore vrai. C'est un état
d'interface de plus, qui n'existe pas dans les maquettes.

> **À reporter dans le cadrage et les maquettes de SleepMaxxer** : un état « en attente du
> réveil » sur le suivi de coucher.

### L'isolement ne se fera pas depuis l'appareil

**Alternative examinée** : le réveil porte lui-même les interrupteurs de sa liaison cloud —
`device.allowuploads` et `backend.dcsenabled` sont à `true`, et l'API accepte l'écriture. Une
piste que les notes du projet n'avaient jamais envisagée : elles n'examinaient que la box
(règle, VLAN, DNS sinkhole). Elle serait faisable en une soirée et réversible.

**Écartée.** Ces interrupteurs dépendent du bon vouloir du firmware : une mise à jour ou un
reset d'usine peut les rétablir **en silence**, et l'appareil garderait de toute façon sa route
vers internet. Un isolement qui peut se défaire tout seul, sans que rien ne le signale, n'est
pas un isolement. Une règle à la box ne dépend de personne.

**Le collecteur garde le rôle d'observateur** : il relit `backend`, `transport` et
`allowuploads`, et rapporte si le réveil est encore relié à Philips. C'est ce qui rend
l'isolement vérifiable dans la durée plutôt que supposé.

### Le collecteur enregistre plus qu'il n'expose

**Tension** : la règle cardinale interdit de construire ce qui ne sert pas un geste réel. Mais
les événements du réveil — alarme déclenchée, rappel, lumière, coucher de soleil — **ne se
remesurent pas**. Ne pas les écrire, c'est les perdre définitivement ; les exposer, c'est
ouvrir la porte à un écran hors périmètre.

**Décision retenue** : enregistrés, **non exposés**. La règle cardinale porte sur la
**surface**, pas sur le stockage. Enregistrer coûte quelques lignes et se rattrape jamais ;
exposer crée une surface qu'il faudra ensuite justifier, tenir et peut-être retirer.

Même raisonnement pour l'**instantané des réglages** : historisé côté collecteur, il permet de
restaurer un réveil réinitialisé sans dépendre d'une synchronisation récente du téléphone.

### Aucune interface web, même une page d'état

**Décision initiale envisagée** : une page d'état minimale, pour voir que la collecte tourne
pendant les semaines où SleepMaxxer n'existe pas encore.

**Écartée.** Le contrôle se fait par l'API et en SSH. C'est le premier endroit où ce projet
aurait pu recommencer FshnRps-GAOE : une page d'état devient un tableau de bord, puis une
consultation, puis SleepMaxxer en double.

**Conséquence assumée** : l'état du système doit être **lisible par l'API** dès le premier
jour, pas ajouté quand l'app arrivera. C'est aussi ce dont l'app a besoin pour distinguer les
deux pannes.

### Les contributions `pysomneo` passent avant la construction

**Ordre arrêté le 2 septembre 2026** : cadrage de SleepMaxxer → le serveur → les contributions.
Argument d'alors : « contribuer *après* avoir construit, l'usage réel fait remonter les vrais
pièges ».

**Ce qui a changé, le 2026-09-06** : cet ordre optimisait la **qualité** de la PR, jamais son
**délai**. Or le délai n'appartient pas au projet — il dépend d'un mainteneur, et une PR ne
vaut pour le portfolio que si elle est fusionnée à temps pour la candidature.

**Décision retenue** : les commentaires sur les issues #16, #13 et #8 et la PR #13 partent
**sans attendre le collecteur**. Ce qui est prêt l'est déjà : les bornes de `brght`, le
décodage binaire de `wusts` et les agrégats `dataupload` sont tous établis par la
rétro-ingénierie, et aucun ne dépend d'un usage réel. Seuls `wungt` et l'abonnement UDP
gagnent à attendre.

**Ce qui remplace l'usage réel : la mesure.** L'ordre du 2 septembre comptait sur des mois
d'utilisation pour faire remonter les pièges. Le délai ne le permet plus — mais ce que l'usage
aurait apporté, une **campagne de tests sur l'appareil** l'apporte plus vite, et sous une forme
citable. Ajouté le 2026-09-06 : **aucune PR ne part avant**

- un **relevé direct sur le réveil** de chaque champ touché, valeurs à l'appui ;
- des **tests automatisés rejouables sans appareil**, sur des réponses réelles enregistrées —
  c'est ce qui permet au mainteneur de les faire tourner sans posséder un HF3671 ;
- la **vérification des cas qui échouent aujourd'hui** : pour `wusts`, les combinaisons de bits
  absentes de la table de huit valeurs magiques ; pour `brght`, les deux bornes et le refus
  au-delà ;
- une **description de PR qui montre la mesure** au lieu de l'affirmer.

Ces contributions sont une pièce de portfolio à traçabilité publique, lue par un chercheur :
une PR fusionnée mais bâclée vaut moins que pas de PR du tout.

---

## 6. Questions ouvertes / à trancher

- [ ] **D'où vient une heure « estimée » ?** Sûr : une heure issue du geste est confirmée.
      Ouvert : le réveil **déduit-il** lui-même une mise au lit de ses capteurs et remplit-il
      `tg2bd` / `tendb` seul, ou l'inférence vivait-elle dans le cloud Philips ? À **observer**
      en instrumentant `wungt` sur plusieurs nuits sans jamais écrire. Le collecteur est le
      seul à pouvoir voir la différence entre une heure qu'il a provoquée et une heure qu'il a
      trouvée. Tant que ce n'est pas établi : rien n'est marqué estimé sans qu'on sache
      pourquoi.
- [ ] **Le palier de cadence retenu.** 1 min est le plancher. Reste à monter par paliers et à
      trouver celui qui tient 24 h sans erreur — 30 s, 15 s, ou rien de plus qu'une minute.
      Décide du volume et de la finesse des courbes.
- [ ] **Ce que fait le réveil isolé à la bascule du 25 octobre 2026.** Applique-t-il son propre
      `dst` / `dstoffset`, ou attend-il qu'on le lui dise ? À instrumenter avant la date. C'est
      dans sept semaines, et ça ne se rejoue que deux fois par an.
- [ ] **L'abonnement UDP.** À essayer avec un `ttl` court et un écouteur prêt, avant d'en
      dépendre — il crée un état persistant sur un appareil en service. S'il fonctionne, il est
      le bon levier pour les événements rares (alarme, rappel, début de nuit) sur un appareil
      qui sature à l'interrogation.
- [ ] **`lgtds`**, champ de lumière du profil d'alarme, listé sans que son sens soit établi.
      Hors périmètre par défaut d'information ; à vérifier sur l'appareil.
- [ ] **Cadence de relevé de `wungt`.** Elle décide de ce qui est perdu dans le cas
      « coucher, lever, recoucher » : une session non figée avant la suivante disparaît. À
      choisir une fois observé à quelle vitesse le champ change réellement.
- [ ] **Fréquence et profondeur de la rotation des sauvegardes locales.** Quotidienne ?
      Combien de générations gardées sur une eMMC de 8 Go ?
- [ ] **Forme du déploiement** — service systemd ou conteneur. Question technique, à trancher
      au plan d'implémentation ; l'exigence fonctionnelle est seulement qu'il redémarre seul et
      survive à une coupure de courant.
- [ ] **Profondeur d'historique servie en une fois.** La pagination est acquise ; la taille des
      lots reste à calibrer sur des mesures réelles.

---

## Suite

1. **Campagne de tests sur l'appareil** — relevé de chaque champ touché, cas limites,
   réponses enregistrées pour rejeu hors appareil. Elle sert **à la fois** les PR et les
   questions ouvertes du §6 (`wungt` sans écriture, essai d'abonnement UDP, cadence
   soutenable, bascule du 25 octobre). Depuis la Radxa : le poste de dev ne peut pas joindre
   le réveil.
2. **Contributions `pysomneo`** — les commentaires sur #16, #13 et #8 peuvent partir dès ce
   soir, ils n'engagent que des faits déjà mesurés et démarrent l'horloge avec le mainteneur.
   Les PR suivent la campagne de tests, dans l'ordre #13 → `wusts` → agrégats `dataupload` →
   `wungt`.
3. **Plan technique puis écriture du collecteur**, sur la base de ce document.
4. **Blocage internet du réveil** à la box, une fois le collecteur en service et l'historique
   constitué — pas avant : couper avant d'avoir sa propre mémoire, c'est perdre des nuits.

> Ce cadrage peut alimenter un plan technique. Les deux décisions qui touchent le contrat avec
> SleepMaxxer (§5, correction d'une nuit passée et appui en attente) sont à reporter dans son
> cadrage, son `AGENTS.md` et les deux notes Obsidian.
