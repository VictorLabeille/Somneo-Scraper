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
