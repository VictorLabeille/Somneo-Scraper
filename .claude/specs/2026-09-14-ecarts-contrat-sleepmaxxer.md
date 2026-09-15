# Écarts entre le collecteur et le contrat de SleepMaxxer

> Statut : **à traiter** · Date : 2026-09-14 · Relevé en lisant le code du collecteur à `4019c88`
> et en interrogeant le collecteur déployé, au moment d'écrire l'application.

Le contrat avec l'application est écrit dans deux documents : le cadrage de SleepMaxxer
(`.claude/specs/2026-09-05-spec-fonctionnelle-sleepmaxxer.md` **du dépôt SleepMaxxer**) et le
cadrage de ce dépôt (`2026-09-06-cadrage-backend-somneo-scraper.md`). Ce document liste ce que le
code déployé ne tient pas de ce contrat. Il ne tranche rien : pour chaque écart, il dit ce qui est
constaté, quelle exigence est manquée, ce que l'application fait en attendant, et une piste.

L'application a été écrite **contre le collecteur tel qu'il est**. Chaque contournement est
signalé ici pour pouvoir être retiré le jour où le collecteur est corrigé.

---

## 1. Une correction d'heure écrase la valeur relevée

**Constat.** `Store.add_night_correction` (`store/db.py`) insère la valeur corrigée dans
`night_correction`, puis fait `UPDATE night SET bedtime|risetime = <valeur corrigée>`. La valeur
relevée d'origine n'est gardée nulle part : `night_correction` ne contient que des valeurs
corrigées, et `raw_tg2bd`/`raw_tendb` sont dans le référentiel du réveil (`wutim`), pas celui du
collecteur.

**Exigence manquée.** Cadrage app §3.E : « la correction est enregistrée par le collecteur, **à
côté** de la valeur relevée, jamais à sa place […] La valeur relevée d'origine reste
consultable. » Cadrage backend §3.C : « la valeur relevée d'origine reste conservée et n'est
jamais écrasée ». Plan technique §5 : « Une correction s'ajoute dans `night_correction`, la
valeur relevée reste. »

**Conséquence.** Après la première correction d'une nuit, son heure mesurée est perdue pour
tout le monde. La correction n'est donc ni « tracée et réversible » ni annulable.

**En attendant, côté app.** Rien à faire : l'app ne peut pas retrouver une valeur que le
collecteur a jetée. Elle affiche la valeur servie.

**Piste.** Deux colonnes de plus dans `night` (`bedtime_observed`, `risetime_observed`) remplies
à la création et jamais modifiées ensuite ; les colonnes servies restent `bedtime`/`risetime`.
Une migration numérotée (le schéma est à `user_version = 1`).

## 2. L'étiquette « corrigé » n'est pas servie

**Constat.** Après une correction, `bedtime_origin` et `risetime_origin` gardent leur valeur
(`confirmed`, `observed`, `estimated`). Rien dans la ligne `night` ne dit qu'une heure a été
corrigée.

**Exigence manquée.** Plan technique §5 : « Une correction ne produit jamais "estimé" : elle
produit "corrigé". » Cadrage backend §2.C : l'API sert « l'origine de chaque heure (confirmée /
estimée / corrigée) ».

**En attendant, côté app.** L'app sait qu'une heure est corrigée seulement quand c'est elle qui l'a
corrigée, ou quand elle relit `GET /v1/nights/{id}` (qui porte la liste `corrections`). Une nuit
reçue par le rattrapage n'a pas cette liste (écart 3).

**Piste.** Passer `bedtime_origin`/`risetime_origin` à `corrected` dans la même transaction que
la correction, ce qui reprend aussi son `seq`.

## 3. Le rattrapage par séquence rend une nuit sans ses corrections

**Constat.** `Store.changes_since` lit les tables `reading`, `window_aggregate`, `night` et
`outage`. `night_correction` n'y figure pas, et la ligne `night` est rendue brute, sans la liste
`corrections` que `GET /v1/nights/{id}` ajoute.

**Conséquence.** Le téléphone reçoit l'heure corrigée sans pouvoir savoir qu'elle l'est. Couplé à
l'écart 1, il ne peut pas non plus montrer la valeur d'origine.

**Piste.** Joindre `corrections` à chaque nuit rendue par `/v1/sync` (les deux modes), ou exposer
`night_correction` comme un genre de plus dans `changes_since`.

## Écarts 1, 2 et 3 — tranchés ensemble par Victor le 2026-09-15

Ils touchent la même ligne servie, et donc la même transaction : ils se traitent ensemble.

**La nuit garde le relevé.** Une correction ne modifie plus `night.bedtime`/`risetime`. Elle
s'ajoute à `night_correction`, qui ne reçoit que des ajouts, et reprend le `seq` de la nuit
pour que le rattrapage la renvoie. La nuit est **servie**, par toutes les routes, rattrapage
compris (les deux modes), avec :

- `bedtime`/`risetime` : la dernière correction du champ, sinon le relevé ;
- `bedtime_origin`/`risetime_origin` : `corrected` si la dernière correction du champ fait foi,
  sinon l'origine du relevé ;
- `bedtime_observed`/`risetime_observed` et `…_observed_origin` : le relevé et son origine,
  toujours ;
- `corrections` : le journal des corrections de la nuit.

Principe 2 du plan §3 : stocker la source, dériver le reste. Motif décisif : `nights.py`
continue d'écrire le relevé (`night_set_rise` à la chute du bit 11). Avec la valeur servie
dérivée, une écriture de la machine ne peut plus écraser une correction ; avec des colonnes
`*_observed` (la piste de l'écart 1), il aurait fallu apprendre à la machine à ne pas le faire.
Aucune migration de données : aucune nuit n'était corrigée sur la carte au 2026-09-15.

**Réversible par une correction « retour ».** `POST /v1/nights/{id}/corrections` avec
`value: null` entre dans le journal comme les autres, et la nuit sert de nouveau le relevé avec
son origine. `night_correction` reste en ajout seul (plan §3). Écartée : la suppression d'une
correction (`DELETE`), qui efface la trace ; et « pas d'annulation », qui ne tient pas le
cadrage app §5 (« tracée et réversible »).

Contrat : tous les champs sont **ajoutés**, aucun n'est renommé. L'app pourra retirer son
contournement, la mémoire locale `corrected` des champs qu'elle a corrigés elle-même : l'origine
servie suffit, et c'est la seule qui voit une correction « retour ».

**Corrigé dans le code le 2026-09-15** (`store/db.py` : `_servir_nuits`, migration v1 → v2 ;
route des corrections ; `tests/test_corrections.py`). Deux tests échouaient sur le mécanisme
avant le correctif : le relevé écrasé (`900 ≠ 1000`), et la correction écrasée par la machine
(`1200 ≠ 1500`). La migration a été éprouvée sur une copie de la sauvegarde du 14 : comptes
intacts, `integrity_check` sans erreur, aucune violation de clé étrangère. Choix
d'implémentation : un retour sans correction en vigueur n'écrit rien et répond `200`, pour
qu'un renvoi après une coupure réseau n'échoue pas. Côté app, un seul type change :
`NightCorrection.value` peut être `null`.

**Non traité** : `day`, et le filtrage des routes par dates (`from`/`to`, `before`), suivent le
relevé. Une correction qui déplace un coucher de l'autre côté de minuit ne change pas le jour de
la nuit. C'était déjà le cas quand la correction écrasait le relevé.

## 4. Le rattrapage par séquence efface le type d'un agrégat

**Constat.** Dans `changes_since`, chaque ligne reçoit `d["kind"] = kind`, où `kind` est le genre
de l'élément (`reading`, `aggregate`, `night`, `outage`). Or `window_aggregate` a **déjà** une
colonne `kind` (`temp`, `hum`, `snd`, `lux`) : elle est écrasée. Vérifié sur le collecteur
déployé :

```json
{"seq": 4, "ts": 1789322619.75, "kind": "aggregate", "avg": 26.4, "lo": 26.4, "hi": 26.5, "hist": null}
```

On ne sait plus si c'est une température ou une humidité. Le mode `before` n'a pas le défaut : il
rend les agrégats par `aggregates_between`, colonne intacte.

**Conséquence.** Tout agrégat arrivé par `since_seq` est inutilisable. C'est une perte de donnée
côté client, silencieuse.

**En attendant, côté app.** L'app stocke les agrégats reçus tels quels pour la sauvegarde, sans
les afficher ; ceux du mode `before` gardent leur type. Aucun écran actuel n'utilise les agrégats.

**Piste.** Nommer le genre de l'élément autrement (`item`, `type`) ; c'est un changement de
contrat, à versionner ou à faire avant que l'app ne dépende de `kind`.

## 5. La cause « collecteur arrêté » n'est jamais écrite

**Constat.** Seuls deux endroits ouvrent une indisponibilité : `collect.py` (`appareil saturé`,
`réveil injoignable`) et `__main__.py` (`carte hors réseau`, `réveil injoignable`). Le
`heartbeat` est une ligne unique réécrite chaque minute ; au redémarrage, rien ne compare le
dernier battement à l'heure courante pour ouvrir une indisponibilité datée.

**Exigence manquée.** Cadrage backend §3.B : « Collecteur arrêté […] une période d'indisponibilité
nommée est enregistrée, bornée par le dernier relevé réussi et le premier de la reprise. »
Cadrage app §3.C : un trou porte sa cause, **fournie par le collecteur**, parmi quatre.

**En attendant, côté app.** Un trou dans la courbe qu'aucune indisponibilité ne couvre s'affiche
« pas de relevé — cause non fournie par le collecteur ». L'app ne déduit pas « collecteur
arrêté » : ce serait inventer la cause.

**Piste.** Au démarrage, avant la première collecte : si `heartbeat.ts` existe et précède
l'instant présent de plus de deux minutes, insérer une `outage` fermée (`start` = dernier
battement, `end` = maintenant, cause `collecteur arrêté`).

## 6. Le rattrapage « récent d'abord » ne porte que les nuits closes

**Constat.** `GET /v1/sync?before=` rend des nuits, et n'y joint points et agrégats que si elles
ont un coucher **et** un lever. Il ne rend ni les points hors des nuits, ni les indisponibilités,
ni les points d'une nuit ouverte ou anormale. Enchaîné comme le prévoit le plan (§7 : première
passe `before`, puis `since_seq` depuis `current_seq`), le téléphone n'aurait jamais ces données.

**Exigence manquée.** Cadrage app §2.C : copie locale **complète**, « nuits et relevés de
capteurs ». Critère d'acceptation : « le collecteur peut être effacé sans perte ».

**En attendant, côté app.** Trois passes, reprenables : (1) `before` sur quelques nuits pour
montrer vite les plus récentes ; (2) `since_seq` depuis la séquence retenue, pour ce qui arrive ;
(3) `since_seq=0` jusqu'à cette séquence, en fond, pour tout le reste. Les points sont dédoublonnés
par `seq`, les nuits par `id`. Le contrat actuel suffit ; c'est seulement plus de requêtes.

**Piste.** Soit rendre dans le mode `before` tout ce qui tombe dans la fenêtre de la page (points,
agrégats, trous), soit documenter la troisième passe comme le mode d'emploi officiel.

## 7. Pas d'instantané des seize profils pour l'export

**Constat.** Le collecteur relit les seize profils chaque jour et les historise dans
`port_change`, mais aucune route ne les sert. `GET /v1/alarms/{n}` relit un profil **sur
l'appareil** (sélection, `PUT wualm {"prfnr": n}`), un à la fois.

**Exigence manquée.** Cadrage app §2.C : l'export contient « les réglages du réveil (alarmes,
thèmes, coucher de soleil), pour qu'un réveil réinitialisé ou remplacé puisse être remis en
état ». Cadrage backend §2.F : l'instantané des réglages « alimente l'export du téléphone ».

**En attendant, côté app.** L'export contient le miroir de `GET /v1/device` (alarmes visibles avec
heure, jours et PowerWake ; `wulgt`, `wudsk`, `wualm/aenvs`, `wualm/aalms`, durée du rappel). Pas
le thème, le son ni l'intensité de chaque profil.

**Piste.** `GET /v1/settings/snapshot` : les derniers corps de `wualm/prfwu` par `prfnr`, plus
les ports de réglage, tels que `port_change` les tient. Lecture seule, aucune requête au réveil.

## 8. Deux routes du plan ne sont pas écrites

**Constat.** Le plan technique §7 liste `GET /v1/outages?from=&to=` et `GET /v1/aggregates?from=&to=`.
Elles répondent `404` sur le collecteur déployé.

**En attendant, côté app.** Non bloquant : les indisponibilités et les agrégats arrivent par le
rattrapage.

**Piste.** Les écrire, ou les retirer du plan.

## 9. Le catalogue ne porte que ce que l'appareil sait

**Constat.** `GET /v1/catalog/themes` rend les ports `files/*` avec `source: "appareil"`. Les
libellés que l'appareil ne publie pas — « No light », « No sound », la radio FM — n'y sont pas.

**Exigence.** Cadrage backend §2.C : correspondance « lue de l'appareil et complétée par le relevé
de SleepMapper, avec sa source citée ».

**En attendant, côté app.** L'app porte elle-même ces libellés, avec leur source
(`docs/sleepmapper/README.md` du dépôt SleepMaxxer). Non bloquant.

## 10. Une indisponibilité reste ouverte à jamais après une redécouverte — **bloque le pilotage**

> Ajouté le 2026-09-14 au soir, en essayant l'app sur émulateur contre le collecteur déployé.
> **À traiter en premier** : c'est le seul écart qui empêche l'app de fonctionner.

**Constat.** `GET /v1/status` rendait `reveil.joignable: false`, cause `réveil injoignable`, avec
deux indisponibilités ouvertes (`end: null`, ids 11 et 15, ouvertes à 12 min d'écart) — alors que
`dernier_releve_at` avançait à la minute : le collecteur lisait le réveil normalement.

**Mécanisme, lu dans le code.** `Collector._echec` (`collect.py`) ouvre une indisponibilité et
garde son id dans `self._outage_id`, un attribut **de l'instance**. Au cinquième échec
(`ECHECS_AVANT_REDECOUVERTE`), `_signaler_perte` fait arrêter ce `Collector` par le superviseur
(`__main__.py`, `Superviseur.run`), qui en crée un neuf après la redécouverte. Le neuf part de
`_outage_id = None` : son premier succès ne ferme rien, et l'indisponibilité de l'ancien reste
ouverte. Chaque perte du réveil qui mène à une redécouverte en laisse une de plus — ici, deux, de
part et d'autre d'une salve de « carte hors réseau ».

**Conséquence.** `Superviseur.gateway()` rend `None` dès qu'une indisponibilité est ouverte : le
relais **refuse toute commande** (`503`, ou le geste de coucher retenu en `202`) alors que le réveil
répond, et l'état annonce « réveil injoignable » à tort. Redémarrer le service ne répare rien : les
lignes restent ouvertes dans la base, et `gateway()` les relit.

**Revu le 2026-09-15.** Toujours actif : ids 11 et 15 ouverts depuis ~12 h (`failures: 9`
chacun — la collecte finit sa tâche en cours après le seuil, d'où plus de cinq échecs), dernier
relevé à moins d'une minute. Second défaut au même endroit : pendant la redécouverte,
`Superviseur._decouvrir` ouvre **ses propres** indisponibilités (`carte hors réseau` ou `réveil
injoignable`) alors que celle de la collecte est encore ouverte. Deux trous se superposent avec
deux causes, et l'app, qui affiche une cause par trou, ne peut pas choisir. Le correctif doit
donc aussi dire qui tient l'indisponibilité en cours quand on passe de la collecte à la
redécouverte.

**En attendant, côté app.** Rien à contourner : l'app affiche ce que le collecteur dit, et désactive
le pilotage — que le relais refuserait de toute façon.

**Piste.** Tenir l'indisponibilité en cours au niveau du superviseur, qui survit aux
redécouvertes, ou fermer au premier succès toutes celles des causes « réveil injoignable » et
« appareil saturé ». Et faire reposer `gateway()` sur l'état de la collecte courante plutôt que sur
la base. Dépannage d'ici là : fermer les lignes ouvertes par `Store.close_outage`, service arrêté —
pas par un `UPDATE` à la main, qui ne reprendrait pas de `seq` et que le rattrapage manquerait.

**Tranché par Victor le 2026-09-15 : un suivi unique.** Un seul objet tient l'indisponibilité en
cours. Le superviseur le crée, comme la machine des nuits, et la collecte comme la redécouverte
l'utilisent : la même cause qui revient incrémente le compteur, une autre cause ferme la ligne
en cours et en ouvre une nouvelle. Deux trous ne se chevauchent donc jamais, et chaque trou a une
seule cause. Seul un relevé réussi ferme l'indisponibilité. `gateway()` interroge ce suivi, plus
la base. Au démarrage, les lignes restées ouvertes sont fermées (par `close_outage`, le `seq` est
repris) au premier relevé qui suit leur début, sinon au dernier battement, et jamais avant leur
début. Les ids 11 et 15 se referment donc d'eux-mêmes au déploiement, à la cadence de `wusrd`
près, sans dépannage à la main. L'autre piste (tout fermer au premier succès) a été écartée : elle
laisse le chevauchement, et sans réparation au démarrage elle aurait daté la fin de 11 et 15 au
jour du déploiement.

**Corrigé dans le code le 2026-09-15** (`collector/src/somneo_collector/outages.py`,
`tests/test_outages.py`). Deux tests écrits avant le correctif reproduisaient l'écart contre le
faux réveil, qui a pour cela un levier `panne`, et échouaient : ligne restée ouverte après une
redécouverte, battement muet pendant la redécouverte. Deux mutations du correctif (suivi non
partagé, battement avant la réparation) sont détectées par la suite. Ce que ce choix a entraîné :

- **le battement bat dans le superviseur, plus dans la collecte.** Il se taisait pendant une
  redécouverte, et la réparation s'appuie sur lui ;
- **la redécouverte ne ferme plus sa ligne à la fin de chaque attente.** Avant, chaque essai
  ouvrait puis fermait sa propre ligne (`failures: 0`) ; maintenant, une ligne reste ouverte et
  compte les essais, jusqu'au premier relevé. Trouver l'adresse ne prouve pas que le réveil
  répond ;
- `GET /v1/status` lit toujours la base. Après la réparation, la base et le suivi disent la même
  chose.

**Éprouvé sur données réelles le 2026-09-15**, sur une copie de la sauvegarde quotidienne du 14
(v1, 21 indisponibilités, prise pendant une redécouverte). 11 est fermée 386 s après son début.
15 est fermée à son début : la sauvegarde n'a aucun relevé après elle, et son dernier battement
la précède de 46 s. C'est le battement qui se taisait pendant la redécouverte, et que ce
correctif fait battre dans le superviseur. Limite : la réparation ne défait pas un chevauchement
passé. 15 recouvre les lignes de redécouverte fermées depuis.

**Reste : déployer sur la carte** et constater que 11 et 15 se ferment : 11 à 386 s de son début,
15 à 2906 s, qui sont leurs premiers relevés, lus sur le collecteur le 2026-09-15
(`/v1/readings`). Une fin datée du jour du déploiement signalerait un défaut.

## 11. Un agrégat est inséré à chaque lecture, pas au changement

> Ajouté le 2026-09-15, en relisant `collect.py` pour l'écart 10.

**Constat.** `Collector.tache_dataupload` appelle `Store.add_window_aggregate` à chaque lecture
(toutes les 5 min, quatre genres), sans comparer avec le précédent. Vérifié sur le collecteur
déployé, dans les 500 premiers éléments de `GET /v1/sync?since_seq=0` : 224 agrégats pour 71
valeurs distinctes, dont une répétée 27 fois, par salves de quatre à 300 s d'écart.

Autre point dans la même table : `hist` est rangé par `json.dumps` puis servi tel quel, donc
comme **une chaîne JSON** (`"{\"ab\": …}"`) et non comme un objet — dans les deux modes du
rattrapage.

**Exigence manquée.** Plan technique §3, table `window_aggregate` : écriture « au changement » ;
§11 point 3, tranché le 2026-09-12 : « au changement pour les ports d'état et les agrégats ».
Principe 3 du §3 : « une valeur inchangée ne se répète pas ».

**Conséquence.** Le rattrapage transporte trois fois trop d'agrégats, chacun avec son `seq` ; une
fenêtre répétée ressemble à plusieurs fenêtres identiques. Rien n'est perdu, mais la même
fenêtre apparaît plusieurs fois.

**En attendant, côté app.** Non vérifié côté app ; aucun écran n'utilise les agrégats (écart 4).

**Piste.** Dédoublonner par genre comme `record_port_change` le fait pour un port, sur `(avg, lo,
hi, hist)` ; décoder `hist` avant de servir. Les doublons déjà en base restent, ou se purgent par
une migration — à trancher.
