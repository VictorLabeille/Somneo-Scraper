# Le lever pris à l'alarme est observé, pas estimé — 2026-09-16

> Instantané daté. Renverse une décision du cadrage backend du 2026-09-06 (§5) et la question
> ouverte §6 du cadrage de SleepMaxxer. Ne pas entretenir ce fichier : pour revisiter le sujet,
> dater une nouvelle spec.

## Ce qui était décidé, et pourquoi c'était faux

Le cadrage backend du 2026-09-06 posait : « une heure issue du geste est *confirmée* ; une heure
de lever issue de l'alarme est *estimée* ». Le collecteur l'applique dans trois écritures de
`collector/src/somneo_collector/store/db.py` — `night_awaiting_rise`, `night_set_rise`,
`night_close`.

**Cette règle n'a jamais été relevée, elle a été posée.** Le cadrage de SleepMaxxer le dit
lui-même en question ouverte §6 : la mention « estimé » est « reprise de SleepMapper sans que sa
règle de production y ait été relevée — et elle ne le sera plus, l'application se videra ».

**Le relevé terrain de Victor l'a renversée** (2026-09-16) :

- la **totalité** de ses nuits dans SleepMapper sont closes par une alarme, mais **1 à 2 par
  mois seulement** portent « estimé » — la règle posée en produirait ~100 % ;
- sur ces nuits-là, l'heure de lever correspond bien à une alarme, comme sur toutes les autres :
  ce n'est donc pas le lever qui les distingue ;
- leurs heures de **coucher** sont plausibles et **non arrondies** (22 h 01). Une saisie manuelle
  arrondirait ; une déduction à partir de capteurs tombe sur l'instant précis où un seuil est
  franchi. C'est donc une **déduction du cloud Philips à partir des capteurs de la chambre**,
  les soirs où le geste « je me couche » n'a pas été fait.

**« Estimé » qualifiait donc un coucher déduit, jamais un lever.** Et le lever pris à
l'extinction de l'alarme est une grandeur que le collecteur **observe** : il voit le bit 11 de
`wusts` retomber, à 10 s près (mesure du 2026-09-12). Le marquer « estimé » était un contresens.

## Ce qui change

`risetime_origin` passe de `estimated` à `observed` dans les trois écritures de `db.py`, et le
journal de `nights.py` suit. Les quatre assertions correspondantes des tests
(`tests/test_nights.py`, `tests/test_corrections.py`) sont alignées.

Le contrat servi ne gagne ni ne perd de champ : seule une valeur d'énumération change. Côté app,
`timeOrigin` faisait déjà tomber `observed` sur « confirmé » ; l'affichage d'une nuit close par
l'alarme passe donc de « estimé » à « confirmé », ce qui est la vérité de la mesure.

## Ce qui ne change pas, et pourquoi

**Le libellé « estimé » reste défini**, côté collecteur comme côté app, alors que plus rien ne le
produit. Décision de Victor du 2026-09-16 : ne rien réécrire tant qu'un producteur peut
apparaître.

Car il peut apparaître. Le collecteur relève déjà la lumière et le bruit de la chambre en
continu (`dataupload`, fenêtres de 15 minutes) : il aurait de quoi déduire un coucher comme le
cloud Philips le faisait. **Rien de tel n'est demandé ici** — c'est une possibilité notée, pas
un périmètre ouvert.

## Ce qui reste ouvert, et se refermera tout seul

Un soir sans geste ne produit **aucune nuit** chez nous : `wungt` ne se remplit qu'au geste, et
`NightTracker.on_wungt` n'ouvre une nuit que sur sa transition. Les 1 à 2 nuits par mois que
SleepMapper estimait vont donc **manquer**, là où elles existaient avant. C'est une régression
fonctionnelle assumée, conséquence directe de l'isolement du cloud.

Elle fournit aussi la vérification définitive de l'hypothèse, sans effort : **la première nuit
qui apparaîtra dans SleepMapper sans exister dans le collecteur la confirmera**. Tant que le
réveil reste relié au cloud (décision du 2026-09-13), la comparaison est possible.

## Report

Les deux `AGENTS.md` ne portaient pas la règle — ils aiguillent, ils ne documentent pas : rien à
y corriger. Le renversement est reporté dans les deux notes Obsidian
(`_agents/Projets/SleepMaxxer/arbitrages.md` et `contrat-backend.md`), et dans les commentaires
de `src/domain/nights.ts` et `src/data/types.ts` du dépôt SleepMaxxer.

**Le collecteur doit être redéployé sur la carte** pour que le changement prenne effet ; les
nuits déjà enregistrées gardent `estimated` et ne sont pas réécrites.
