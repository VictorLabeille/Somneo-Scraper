# `probes/` — les sondes de mesure

Sondes de diagnostic lancées **depuis la Radxa** : le poste de dev ne peut pas joindre le réveil
(WSL2 est derrière un NAT que le multicast SSDP ne franchit pas — `docs/radxa.md`).

Elles n'ont **aucune dépendance** : bibliothèque standard seulement, rien à installer sur la carte.
`somneo_probe.py` porte le socle commun — découverte SSDP, `GET` qui ne lève jamais, lecture du tas.

> ⚠️ **Une vingtaine écrivent sur l'appareil** — lumière, son, afficheur ; aucune sur `wualm` ni
> `fac`. **Leur nom ne le dit pas** : lire le code d'une sonde avant de la lancer.

Elles fondent les chiffres publiés dans `docs/somneo-api.md` : une mesure qu'on ne peut plus
rejouer ne se défend pas. Chaque sonde dit en tête ce qu'elle établit, et si elle écrit.

## Les principales

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
| `ecriture_heure.py` | Que l'heure du réveil ne s'écrit pas — le résultat négatif qui a fermé la question |
| `derive_horloge.py` | La dérive de l'horloge, ~10 s par jour |
| `sndss.py` | Le départ en douceur : inscriptible, non borné, sens physique inconnu |

Il en existe une cinquantaine ; le tableau ne reprend que celles qui portent une conclusion citée
ailleurs.

## `results/`

Relevés bruts, **corps de réponse retirés** : ils portaient le numéro de série de l'appareil, son
adresse MAC et les conditions de la chambre. La valeur probante — latences, échecs, tas — est
intacte. Le dépôt est public, et les heures de sommeil y sont passées une fois (2026-09-08).

Les **captures longues ne sont pas versionnées** : mêmes données personnelles, en continu. Leur
place est la base du collecteur.

Les résultats chiffrés sont interprétés dans `docs/somneo-api.md`.

## Les lancer

Les pièges qui ont coûté cher — `pkill`/`pgrep` qui se voient eux-mêmes et coupent la session ou
mentent, comparaison d'heures en texte, `SIGSTOP` au milieu d'une requête — sont dans les règles
impératives de l'`AGENTS.md` du dépôt. Les lire avant de lancer une campagne.
