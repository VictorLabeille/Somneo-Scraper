# API locale du Somneo — rétro-ingénierie de SleepMapper

> Document de référence produit le 31 août 2026 par rétro-ingénierie de l'application
> **SleepMapper 3.22.0-rc.1** (`com.philips.src.hss`) croisée avec un relevé direct de
> l'appareil. Il décrit le protocole que `Somneo-Scraper` doit parler et **ce que
> l'application constructeur fait qui n'est pas réplicable localement**.
>
> Aucune donnée sensible ici : la clé du port `security`, le numéro de série, l'adresse MAC
> et le SSID relevés sur l'appareil sont **volontairement omis** — ce dépôt est public.

## Appareil étudié

| Champ | Valeur |
| --- | --- |
| Modèle | Philips Somneo **HF3671/01** (`modelid` 884367101010) |
| Nom de code interne | **BrightEyes** (`BEYE-PROD-WF`, cf. le CN du certificat TLS) |
| Firmware | `pkgver` 30406, `swverwifi` 2.2.5, `swveruictrl` R1.59.000.PRD |
| Système | ThreadX 5.6, pile UPnP 2.0 |
| Adressage | DHCP — **ne jamais coder l'IP en dur**, passer par SSDP |

## 1. Découverte

M-SEARCH SSDP sur `239.255.255.250:1900` avec :

```
ST: urn:philips-com:device:DiProduct:1
```

La réponse porte `SERVER: ThreadX/5.6 UPnP/2.0 Wake-up Light/1` et un `LOCATION`
pointant `http://<ip>/upnp/description.xml` (HTTP **en clair**, port 80), qui renvoie
`friendlyName`, `modelNumber` (`HF367x`) et le `cppId`.

> Le multicast ne traverse pas le NAT de WSL2 : la découverte doit être lancée depuis un
> hôte réellement sur le LAN (la Radxa).

## 2. Transport

```
https://<ip>/di/v1/products/{0|1}/{port}
```

Confirmé par `LanRequest.createURL()` dans l'APK :
`"https://" + host + "/di/v" + version + "/products/" + productId + "/" + portName`.

- **TLS auto-signé**, valide jusqu'en 9999 (`CN = Sleep and Respiratory Care (HSS), BrightEyes 1`).
  Vérification à désactiver côté client.
- `GET` pour lire, `PUT` (corps JSON) pour écrire. L'écriture ne renvoie que les champs modifiés.
- En-tête `connection: keep-alive` envoyé par l'app.
- Codes d'erreur observés : `422 {"error":"No such Port"}` / `{"error":"Unknown port"}`,
  `404 {"error":"Unknown product"}`, `400 {"error":"Not understood"}`,
  `500 {"error":"Timeout"}` (l'appareil sature — voir §6), `501 {"error":"Not implemented"}`.

**Ce que la couche TLS accepte — mesuré le 2026-09-07** (`probes/tls.py`) :

| | |
| --- | --- |
| Versions acceptées | **TLS 1.2 uniquement.** TLS 1.0, 1.1 et **1.3** sont refusés |
| Suite négociée | `ECDHE-RSA-AES128-GCM-SHA256`, 128 bits |
| Certificat | 1 146 octets (DER) |

Le refus de **TLS 1.3** est le point qui compte : une distribution récente dont la politique
`openssl` exigerait TLS 1.3, ou un client qui le forcerait, ne parlerait tout simplement pas à
l'appareil. À garder en tête si le collecteur change un jour d'hôte ou d'image de base.

**Méthodes HTTP.** Seuls `GET` et `PUT` existent. `POST` et `PATCH` sont **refusés au niveau
TCP** (`connection refused`) ; `HEAD`, `OPTIONS` et `DELETE` ne répondent pas du tout et
**expirent**. Sonder l'appareil avec un client qui tente un `HEAD` préalable, c'est donc
attendre le timeout pour rien.

**Corps mal formé sur un `PUT`** — la distinction est nette et utile pour diagnostiquer :

| Corps envoyé | Réponse |
| --- | --- |
| JSON tronqué, non-JSON, tableau au lieu d'objet, corps vide | `400 {"error":"Not understood"}` |
| JSON valide, **clé inconnue** | `422 {"error":"No such Property"}` |

Autrement dit `400` = « je n'ai pas su lire », `422` = « j'ai lu, ce champ n'existe pas ».

### Authentification — il n'y en a pas

L'app implémente un schéma de défi/réponse (`PhilipsCondorScheme`) : sur `401`, l'appareil
renvoie `WWW-Authenticate: <scheme> <défi base64 de 16 octets>` et le client répond un
`Authorization` dérivé du couple clé client / secret partagé du port `security`.

**Ce chemin n'est jamais emprunté sur ce firmware.** Vérifié le 31 août 2026 : tous les `GET`
et un `PUT` (`wutms` ← `{"fmthr":true}`) passent en `200` **sans en-tête `Authorization`**.
Le port `pairing` répond `501 Not implemented` et `device.allowpairing` vaut `false`.

> Conséquence pour le projet : **SleepMaxxer peut lire et piloter le réveil sans appairage
> ni compte Philips.** C'est l'hypothèse centrale du projet, désormais validée.

## 3. Ports du produit 0 — plateforme

L'appareil s'auto-décrit : `GET /di/v1/products/0/` renvoie la liste de ses ports.

| Port | Contenu |
| --- | --- |
| `sub` | Abonnements de notification actifs (révèle des ports non listés ailleurs) |
| `log` | `{"msg":""}` |
| `firmware` | `name`, `version`, `state`, `progress`, `canupgrade`, `maxchunksize` |
| `locale` | `country`, `timezone` (ex. `FR` / `Europe/Paris`) |
| `pairing` | **501** — non implémenté |
| `security` | `key`, `nextkey` — secret du schéma de défi. Lisible sans authentification |
| `time` | `datetime`, `dst`, `dstchangeover`, `dstoffset`, `timezone`, `calday` |
| `wifi` | SSID, IP, masque, passerelle, DHCP, MAC, `cppid`, SSID « voyage ». `password` vide |
| `wifisettings` | Réseaux mémorisés |
| `schedules` | `{}` |
| `backend` | **URL du cloud Philips + état de la liaison** — voir §5 |
| `transport` | État de la session cloud (`state: closed`) |
| `mem` | Tas ThreadX : `heap_size` 87 200 o, `heap_free` ~25 ko. Explique les timeouts |
| `logsettings` | `contractid`, niveaux de log, `uploadperiod` |
| `compliance` | Paramètres usine (pays, SPI) |

## 4. Ports du produit 1 — fonctions du réveil

`GET /di/v1/products/1/` **expire systématiquement** (`500 Timeout`, cinq tentatives
jusqu'à 60 s, appareil au repos) : il n'a pas assez de tas pour sérialiser l'index. La liste
ci-dessous vient donc de l'APK (`com.philips.cdp2.brighteyes.ports.*`), chaque entrée ayant
été vérifiée en direct.

> **Exhaustivité — mesurée le 2026-09-07, et c'est un résultat, pas une estimation.** Le
> produit 0 s'auto-décrit ; le produit 1 non, son index expire (voir §7). La liste ci-dessous
> venait donc de l'APK, avec la mise en garde qu'un port exposé par le firmware mais ignoré
> de SleepMapper n'y figurerait pas. **Deux balayages exhaustifs ont levé le doute, chacun
> sur son domaine** (`probes/balayage.py`, 17 576 noms chacun, ~150 min) :
>
> | Domaine balayé | Réponses obtenues | Résultat |
> | --- | --- | --- |
> | `wu` + 3 lettres | **17 576 / 17 576** — aucun trou | **13 ports, tous connus.** Aucun inconnu |
> | 3 lettres, sans préfixe | 17 505 au premier passage, **71 rejoués** ensuite | **`dsi` et `fac`**, tous deux nouveaux |
>
> **Le second balayage n'était pas complet au premier passage** : 71 noms consécutifs
> (`ddu` → `dgm`) n'ont rendu ni `422` ni réponse — 67 « connection refused », 3 timeouts, une
> erreur TLS. L'appareil avait décroché sur un bloc puis s'était remis ; ces 71-là n'étaient
> donc pas testés, seulement manqués. Rejoués le soir même (`probes/rattrapage_balayage.py`),
> **ils répondent tous `422`**. C'est seulement à partir de là que le mot « exhaustif »
> s'applique — un balayage se juge au nombre de **réponses**, pas au nombre de requêtes.
>
> Conséquence : **pour les noms en `wu` de cinq lettres, la liste est complète — c'est prouvé,
> plus un minorant.** Les 13 sont `wualm`, `wudsk`, `wufmp`, `wufmr`, `wulgt`, `wungt`,
> `wuply`, `wurlx`, `wusrd`, `wusts`, `wutim`, `wutmr`, `wutms`. Hors de ce domaine la liste
> reste un minorant : les noms de 4 lettres (456 976 combinaisons) et ceux de 5 lettres sans
> préfixe `wu` n'ont pas été balayés. **Ce n'est pas hors de portée** — voir §7, « Coût d'un
> `422` » : ~66 h au rythme des sondes actuelles, mais **environ 2 h** en réutilisant la
> connexion. C'est un choix de priorité, pas une impossibilité.

| Port | Classe SleepMapper | Rôle | `pysomneo` |
| --- | --- | --- | --- |
| `wusrd` | `SensorPort` | Capteurs | ✅ |
| `wusts` | `StatusPort` | État courant (bitmask) | ✅ |
| `wualm` | `AlarmPort` | Racine alarmes (`snztm`, `prfnr`) | ✅ |
| `wualm/aenvs` | `AlarmVisibilityPort` | Activation/visibilité des 16 profils | ✅ |
| `wualm/aalms` | `AlarmMomentPort` | Heures et jours des 16 alarmes | ✅ |
| `wualm/alctr` | `AlarmWritePort` | `tapsz` (snooze), `disms` (arrêt) | ✅ |
| `wualm/prfwu` | `AlarmProfilePort` | Détail d'un profil d'alarme | ✅ |
| `wudsk` | `DuskSettingsPort` | Coucher de soleil | ✅ |
| `wulgt` | `LightPreviewPort` | Lumière et veilleuse | ✅ |
| `wuply` | `PlayingPort` | Lecteur audio | ✅ |
| `files/*` | `FilesPort` | Thèmes lumineux et sons | ✅ |
| `wungt` | `BedTimePort` | **Suivi de nuit / heures de coucher** | ❌ |
| `wurlx` | `RelaxBreathSettingsPort` | RelaxBreathe | ❌ |
| `wutmr` | `TimerPort` | Minuteurs en cours | ❌ |
| `wufmr` | `FMRadioPort` | Radio FM | ❌ |
| `wufmp/00` | `FMRadioPresetListPort` | 5 présélections FM | ❌ |
| `wutms` | `TimeSettingsPort` | Format horaire, fuseau, source de temps | ❌ |
| `device` | `BEDevicePort` | Identité, versions, `allowuploads` | ❌ |
| `dataupload` | `DataUploadPort` | **Téléversement cloud** — voir §5 | ❌ |
| `dataupload/event.1` | — | Dernier **type** d'événement, sans date — voir §5 | ❌ |
| `wifiui` | `DeviceConnectionPort` | État WiFi, **RSSI** | ❌ |
| `fac` | — | Réinitialisation usine (`{"wifi":0,"reset":0}`) | ❌ |
| `wutim` | **aucune** | **Horloge locale de l'appareil** — découvert le 2026-09-06 | ❌ |
| `dsi` | **aucune** | `{"keypress":"","screenid":"NA"}` — découvert le 2026-09-07 | ❌ |

### `wutim` — un port que l'application n'utilise pas

Trouvé le 6 septembre 2026 par balayage de noms (`probes/exploration.py`), et **absent de
partout** : ni dans l'APK décompilé, ni dans l'issue #16, ni dans `pysomneo`, ni dans les
relevés du 31 août. C'est le seul port inconnu qu'un balayage ciblé de 54 noms ait fait
apparaître — les 53 autres ont répondu `422`.

```json
GET wutim → {"yrltm":2026,"moltm":9,"dtltm":6,"hrltm":22,"miltm":25,"scltm":30,"daynm":6}
```

Suffixe `ltm` = *local time* : année, mois, jour, heure, minute, seconde, et `daynm` le jour de
la semaine. C'est l'horloge telle que l'appareil la voit, en composants décomposés — là où
`/di/v1/products/0/time` la donne en ISO 8601.

Intérêt direct pour le collecteur : c'est une seconde source pour mesurer la dérive après
l'isolement, et elle ne dépend pas du même chemin de code que le port `time`. À ne pas
confondre avec le **champ** `wutim` du port `wusts`, qui chronomètre la séquence de réveil en
cours (voir §4, sémantique de `wusts`).

Deux ports de l'APK **n'existent pas** sur le HF3671 (`422`) — ils visent d'autres modèles :
`wuwdw` (`WindDownDuskPort`) et `wusds/prfds/01` (`ScheduleSunsetPort`).

### Sémantique des champs

Extraite des annotations `@SerializedName` des modèles de l'APK, qui ont survécu à
l'obfuscation.

**`wusrd` — capteurs.** Le modèle de l'app déclare 21 champs ; le HF3671 n'en renvoie que 8.

| Clé | Sens | Renvoyé par le HF3671 |
| --- | --- | --- |
| `mslux` `mstmp` `msrhu` `mssnd` | Mesure instantanée : lumière (lux), température (°C), humidité (%), bruit (dB) | ✅ |
| `avlux` `avtmp` `avrhu` `avsnd` | Moyennes glissantes | ✅ |
| `dylux` `dytmp` `dyrhu` `dysnd` | Moyennes de jour | ❌ |
| `ntlux` `nttmp` `ntrhu` `ntsnd` | Moyennes de nuit | ❌ |
| `msco2` `avco2` `dyco2` `ntco2` | CO₂ — autre modèle | ❌ |
| `enscr` | « Energized score » | ❌ |

**`wungt` — suivi de nuit.** C'est la source des heures de coucher de SleepMapper.

> **Convention d'horodatage de ce document.** Les heures murales du propriétaire ne sont pas
> publiées : ce dépôt est public, et le rythme de sommeil de quelqu'un n'a pas à y figurer. Les
> instants sont donnés **en écart par rapport à un repère nommé** — `C` l'appui « je me couche »,
> `A` l'heure programmée de l'alarme. **Les écarts, eux, sont exacts** : ce sont eux qui portent
> les démonstrations, et ils se rejouent tels quels. Les captures de nuit ne sont pas versionnées.

| Clé | Sens |
| --- | --- |
| `tg2bd` | Horodatage de mise au lit (`go to bed`) — **provient du geste**, vérifié le 2026-09-06 |
| `tendb` | Horodatage de sortie du lit (`end bed`) — **écrit à la clôture de la session**, jamais au geste, voir ci-dessous |
| `ntstr` `ntend` `ntlen` | Début, fin et durée de nuit. **Vides sur cet appareil, pas sur tous** |
| `night` | Session de nuit active — **seul champ écrit par l'app** (`getKeyMapForNight`) |

> **`wungt` ne mesure rien — confirmé par une nuit entière, le 2026-09-07.** Le port ne se
> remplit **que** sur le geste de l'utilisateur dans l'application. Sans ce geste, il ne se
> passe rien : ni détection, ni horodatage, ni remise à zéro.
>
> **L'expérience.** Nuit du 6 au 7 septembre 2026 : le propriétaire se couche **sans** appuyer
> sur « je me couche ». La capture interroge `wungt` toutes les 60 s pendant **22 h 15
> consécutives** — **949 relevés**. Résultat : *une seule* valeur sur les 949, identique du
> premier au dernier.
>
> ```json
> {"tg2bd":"<ISO 8601 avec décalage>", "tendb":"<tg2bd + 12 h 00 min 00 s>",
>  "ntstr":"", "ntend":"", "ntlen":"", "night":false, "gdngt":false, "gdday":false}
> ```
>
> `tg2bd` est resté sur l'appui de la **nuit précédente**, avec **22 h de retard** ; le coucher
> réel, lu à la minute sur la chute de `mslux` (voir ci-dessous), est tombé près de 22 h **après**
> l'horodatage que le champ affichait. Aucun champ n'a bougé : le réveil n'a **pas** vu la nuit
> passer.
>
> Deux faits en découlent, tous deux dirimants pour le collecteur :
>
> 1. **`tg2bd` est un champ périmable, jamais vide.** Il ne dit pas « la dernière nuit », il dit
>    « le dernier appui, quand qu'il ait eu lieu ». Le lire sans le dater contre autre chose,
>    c'est attribuer à cette nuit le coucher d'une nuit quelconque. Un collecteur doit le
>    traiter comme suspect tant qu'il ne l'a pas vu *changer*.
> 2. **`tendb` n'est pas posé au geste : il est écrit à la clôture de la session.** Et une
>    session que rien ne clôt se ferme d'elle-même **au bout de 12 h**. C'est la nuit du 7 au 8
>    (plus bas) qui le dit, et elle **corrige la lecture du 2026-09-07** : le `tg2bd + 12 h`
>    n'était pas une formule d'écriture, c'était une expiration.
>
>    Les deux relevés à +12 h à la seconde près sont, dans cette lecture, deux sessions **restées
>    ouvertes** :
>
>    | Source | Écart `tendb` − `tg2bd` | Ce qui a fermé la session |
>    | --- | --- | --- |
>    | Ce HF3671/01, appui du 6 sept. 2026 | **+12 h 00 min 00 s** | rien — expiration |
>    | **Frank071, issue #16, 16 févr. 2024** — autre appareil, valeurs publiées par lui | **+12 h 00 min 00 s** | rien — expiration |
>    | **Ce HF3671/01, nuit du 7 au 8 sept. 2026** | **+7 h 21 min 06 s** | l'alarme |
>
>    Le couple du 6 septembre n'a été **écrit qu'une fois**. Les 949 relevés de la nuit suivante
>    le retrouvent identique, mais c'est la *même* valeur figée : les relire ne fait pas une
>    seconde mesure. Compter deux nuits ici serait compter deux fois le même fait — erreur
>    commise le 2026-09-07 avant vérification.
>
>    Le relevé de Frank071 reste décisif pour la portée : autre appareil, autre firmware, deux
>    ans plus tôt, et un `tg2bd` à **11 h du matin** — personne ne se couche à 11 h 07 pour se
>    lever à 23 h 07. Le délai de 12 h est posé par le firmware du modèle, pas par notre
>    exemplaire.
>
>    **Ce qui reste vrai pour le collecteur, et c'est l'essentiel : `tendb` ne date jamais un
>    lever.** Session close par l'alarme, il vaut l'heure **programmée** de l'alarme ; session
>    expirée, il vaut le coucher + 12 h. Dans les deux cas c'est une heure prévue ou calculée,
>    et dans aucun une heure observée.
>
> **Ce qui reste pour dater une nuit, alors : la lumière.** Elle est mesurée, elle, et elle est
> nette. Le plafonnier s'éteint entre deux relevés consécutifs de `wusrd` :
>
> ```
> E − 61 s   mslux = 110.6      ← plafonnier allumé
> E          mslux =   0        ← éteint
> ```
>
> (`E` = le relevé où l'extinction est constatée, cadence 60 s.)
>
> Une minute d'incertitude, sans aucun geste demandé à l'utilisateur — là où `tg2bd` exige un
> appui et se tait s'il n'a pas lieu. Attention en revanche à ne pas confondre avec `avlux`,
> qui est une moyenne **retardée** : elle n'a répercuté l'extinction qu'à `E + 4 min`.
>
> **`ntstr` / `ntend` / `ntlen` ne sont pas vides partout — et notre observation est étroite.**
> Le relevé de Frank071 dans l'issue #16 (16 février 2024, autre appareil) donne
> `"ntend":"07:00"` et `"ntlen":"07:00"` alors que `night` vaut `false` : renseignés **hors
> session**, et au format `HH:MM`, pas en ISO 8601 comme `tg2bd`/`tendb`. **Ne pas coder « ces
> champs sont toujours vides »** : un client doit accepter les deux cas.
>
> **Portée de ce qui a été mesuré.** La mention « vides hors session » venait du 31 août et
> n'était alors qu'une lecture de l'APK (`getKeyMapForNight`), pas une mesure. Les trois
> situations sont désormais observées, la dernière depuis la nuit du 7 au 8 septembre :
>
> | Situation | Observée ? |
> | --- | --- |
> | Nuit **sans** appui (6→7 sept.), 949 relevés | oui — les trois champs vides |
> | État résiduel après une nuit **avec** appui (5→6 sept.), 22 h durant | oui — les trois champs vides, `tg2bd` figé sur l'appui |
> | **Session active (`night: true`)** (7→8 sept.), 435 relevés | **oui — voir ci-dessous** |
>
> **Ce que fait une session ouverte — nuit du 7 au 8 septembre 2026.** Le cas qui manquait a été
> capturé : le propriétaire a appuyé sur « je me couche » dans SleepMapper. `wungt` interrogé
> toutes les 60 s, **435 relevés avec `night: true`**, de 23 h 29 min 24 s à 06 h 49 min 41 s —
> 7 h 20 de session.
>
> | Instant | Ce qui change dans `wungt` |
> | --- | --- |
> | `C` — l'appui | `night` → `true`, `tg2bd` → **l'horodatage de l'appui, à la seconde** |
> | `C` → `C + 7 h 20 min 47 s` | **rien.** 435 relevés strictement identiques |
> | `C + 7 h 21 min 06 s` = `A` — l'alarme | `night` → `false`, `tendb` → **l'heure programmée `A`** |
>
> Cinq réponses, dont quatre négatives :
>
> 1. **`ntstr` / `ntend` / `ntlen` restent vides pendant toute la session.** Ils ne sont donc pas
>    « remplis à l'ouverture puis vidés » : sur cet appareil ils ne se remplissent **jamais**.
>    L'écart avec l'appareil de Frank071 n'est toujours pas expliqué — mais il ne s'explique
>    **pas** par l'état de la session, et cette piste-là est close.
> 2. **`tendb` ne bouge pas au geste.** Pendant les 7 h 20, il garde la valeur périmée du
>    6 septembre : un client qui lirait `tendb` en cours de session lirait l'avant-veille.
> 3. **`gdngt` et `gdday` restent `false`** de bout en bout — ni l'appui, ni l'alarme ne les
>    lèvent. Leur rôle reste inconnu.
> 4. **`wusts` ne trahit rien.** Il vaut `1` du coucher au lever : la session de nuit n'existe
>    que dans `wungt`, aucun autre port interrogé cette nuit-là ne la laisse voir.
> 5. **La session se ferme sur l'alarme elle-même, et non sur l'utilisateur.** `tendb` prend
>    l'heure **programmée** `A`, à la seconde, et la bascule tombe entre les relevés
>    `C + 7 h 20 min 47 s` et `C + 7 h 21 min 48 s` — soit de part et d'autre de `A`. Or le bouton
>    d'arrêt n'a été pressé qu'**après** : `wusts` passe de 2817 (sonnerie) à 258 à `A + 66 s`.
>    La session était déjà close.
>    Le propriétaire l'a confirmé le 2026-09-08 : au lever, **aucun geste dans l'application**,
>    seulement le bouton du réveil. C'est donc le firmware qui clôt la nuit, à l'heure prévue de
>    l'alarme — ni le geste de l'utilisateur, ni l'instant réel de l'arrêt de la sonnerie.
>
> **L'ancrage par la lumière tient une seconde fois, et le geste le confirme.** Le plafonnier
> s'éteint, l'appui suit deux à trois minutes plus tard :
>
> ```
> C − 2 min 31 s   mslux = 5.9    ← le plafonnier s'éteint
> C                ← « je me couche » (tg2bd)
> C + 29 s         mslux = 0
> ```
>
> C'est la deuxième nuit où `mslux` donne le coucher à la minute. Là où le geste manque — et il
> a manqué la nuit précédente — elle reste la seule source.

**`wualm/prfwu` — profil d'alarme.** `prfnr` (n° 1-16), `pname`, `prfen` (activé),
`prfvs` (visible), `almhr`/`almmn`, `daynm` (masque de jours), `ayear`/`amnth`/`alday`
(date pour une alarme unique), `curve` (intensité du lever de soleil), `durat` (durée),
`ctype` (thème lumineux), `snddv`/`sndch`/`sndlv`/`sndss` (source, canal, volume, départ
en douceur), `pwrsz`/`pszhr`/`pszmn` (PowerWake), `snztm` (snooze), `lgtds`.

Masque `daynm` : bit 1 = lundi … bit 7 = dimanche. `62` = jours ouvrés, `192` = week-end,
`254` = tous les jours, `0` = demain uniquement.

> **Extrait figé en anglais pour l'amont** : [`wusts-bitfield.en.md`](wusts-bitfield.en.md),
> daté du 2026-09-09, écrit pour `pysomneo` et son intégration Home Assistant. **Ce fichier-ci
> reste la source** ; l'extrait ne sera pas mis à jour et ne fait pas autorité.

**`wusts` — état, et son décodage binaire.** `pysomneo` traite le champ `wusts` comme une
table de 8 valeurs magiques (`1: off`, `2: sunset`, `257: light-on`, `2321: snooze`…), ce qui
échoue dès qu'une combinaison non listée se présente. C'est en réalité un **champ de bits**,
et `StatusProperties` en donne les tests exacts :

| Bit | Signification (certaine — issue du code de l'app) |
| --- | --- |
| 0 | Composante « veille » |
| 1 | Menu utilisateur affiché |
| 2 | Alarme active |
| 4 | Alarme en snooze |
| 11 | **Appareil actif** (bit maître : 0 = veille) |

```
isStandBy()      = bit11 == 0 && bit0 == 1
isUserMenu()     = bit11 == 0 && bit1 == 1
isAlarmActive()  = bit2 == 1  || bit11 == 1
isAlarmSnoozed() = bit4 == 1  && bit11 == 1
```

Recoupé avec la table de `pysomneo`, trois bits supplémentaires se déduisent — **hypothèse,
non confirmée par le code** : bit 3 = coucher de soleil, bit 8 = lumière allumée,
bit 9 = son actif. (`2321` = bits 0,4,8,11 → snooze ✓ ; `2309` = bits 0,2,8,11 → réveil ✓ ;
`257` = bits 0,8 → lumière seule ✓.)

**Le modèle en bits explique la table de `pysomneo` en entier, y compris ce qui lui manque.**
Décomposées, ses huit entrées sont toutes des combinaisons cohérentes — et l'une des absences
s'explique d'elle-même :

| Valeur | Bits | Lecture | Statut |
| --- | --- | --- | --- |
| `1` | 0 | veille | mesuré |
| `2` | 1 | transitoire d'extinction | mesuré |
| `257` | 0, 8 | lumière, depuis le repos | mesuré |
| **`258`** | **1, 8** | **lumière, allumée pendant le transitoire — absent de la table** | mesuré |
| **`264`** | **3, 8** | **coucher de soleil sans son, en soirée — absent de la table** | mesuré |
| **`265`** | **0, 3, 8** | **le même, trois heures plus tard — absent aussi** | mesuré |
| **`320`** | **6, 8** | **RelaxBreathing — absent de la table** | mesuré |
| **`513`** | **0, 9** | **lecteur audio (aux ou FM) — absent de la table** | mesuré |
| **`769`** | **0, 8, 9** | **lecteur audio + lampe — absent de la table** | mesuré |
| `776` | 3, 8, 9 | coucher de soleil **avec** son | mesuré |
| `777` | 0, 3, 8, 9 | idem + bit 0 — **l'écart avec `776` reste inexpliqué** | mesuré |
| `2309` | 0, 2, 8, 11 | aube de l'alarme | mesuré |
| `2817` | 0, 8, 9, 11 | phase sonore | mesuré |
| `2321` | 0, 4, 8, 11 | rappel — jamais reproduit ici | table amont |

**`776` est exactement `264` plus le bit 9, et c'est mesuré, pas déduit.** Le coucher de soleil
de cet appareil est réglé sans son (`wudsk.snddv` = `"off"`) et vaut `264` ; le même, relancé
avec `snddv: "dus"` au volume 1, vaut **`776` — six fois sur six**, la valeur exacte que porte
la table de `pysomneo`. La valeur absente n'est donc **pas une particularité de cet
exemplaire** : c'est le même état à un réglage près. Cela répond à la question « et si cela
changeait d'un Somneo à l'autre » — ce qui change n'est pas le modèle, c'est le son.

**`777` a fini par être reproduit — mais l'écart avec `776` reste inexpliqué.** Le coucher de
soleil sonore a d'abord donné `776` (six fois, deux contextes), puis `777` (neuf fois) plus tard
dans la même soirée. L'hypothèse testée — la source configurée dans le PUT de démarrage contre
une source déjà en place — est **fausse** : les deux formes donnent `777`, trois fois chacune.
Ce qui fait apparaître ou disparaître le bit 0 sur un coucher de soleil sonore n'est donc pas
identifié. **Ne rien affirmer là-dessus.** Ce qui compte pour l'usage : les deux valeurs
existent sur le même appareil, et la table amont les porte toutes les deux.

**`2321` (rappel) reste le seul état de la table jamais reproduit ici** — il demande une vraie
alarme suivie d'un appui sur le rappel, donc une observation du matin, pas une écriture.

**Le bit 6 est inédit** : RelaxBreathing vaut `320` (bits 6 et 8), huit fois sur huit et dans
les deux contextes. Le bit n'apparaît ni dans `StatusProperties`, ni dans la table de
`pysomneo`, ni dans l'issue #16. RelaxBreathing **écrase** l'état lumineux au lieu de s'y
ajouter : lampe allumée (`257`) puis RelaxBreathing donne `320`, pas une combinaison.

**Le modèle se vérifie par composition, et c'est la démonstration la plus forte.** Le lecteur
audio seul vaut `513` (bits 0 et 9) ; en allumant la lampe par-dessus, on obtient `769` — soit
`513 + 256`, le bit 8 venant s'ajouter sans rien déplacer. Deux actions indépendantes, deux
bits indépendants : `wusts` n'est pas une énumération d'états, c'est un champ de bits.

**⚠ Le coucher de soleil ne vaut pas une valeur stable : `264` ou `265` selon l'état de
l'appareil.** Mesuré six fois à 19 h (`264`) et six fois à 22 h (`265`), toujours depuis un
départ à `1`. Les deux voies d'écriture ont été comparées dans la même minute — `PUT wudsk
{"onoff": true}` en direct et `toggle_sunset()` de `pysomneo` — et donnent **le même
résultat** : ce n'est pas la bibliothèque.

**Ce qui lève le bit 0 : l'absence prolongée de sollicitation lumineuse.** Trois hypothèses ont
été écartées par la mesure avant d'arriver là — la voie d'écriture, la durée de repos immédiat
(`265` à 10 s comme à 60 s) et la luminosité ambiante (classée « clair » dans les deux
campagnes). La quatrième tient, et elle est venue d'une relecture des protocoles : la campagne
de 19 h **allumait la lampe avant chaque essai** pour fabriquer son contexte de départ, celle de
22 h non.

`prealable_bit0.py` le montre en cinq essais alternés :

| Essai | Ce qui précède | `wusts` |
| --- | --- | --- |
| 1 | rien depuis ~1 h 30 | **265** |
| 2 | lampe allumée puis éteinte | 264 |
| 3 | rien — mais la lampe a servi à l'essai 2 | **264** |
| 4 | lampe | 264 |
| 5 | rien | 264 |

**L'effet persiste**, mais brièvement : une sollicitation lumineuse éteint le bit 0, et il
revient **entre 15 et 30 secondes** après l'extinction de la lampe.

| Délai après la lampe | 15 s | 30 s | 60 s | 90 s | 120 s | 5 min |
| --- | --- | --- | --- | --- | --- | --- |
| `wusts` du coucher de soleil | **264** | 265 | 265 | 265 | 265 | 265 |

**Conséquence pour tout consommateur de `wusts` : les deux valeurs se croisent en usage
ordinaire.** Trente secondes séparent l'une de l'autre — un utilisateur qui éteint sa lampe puis
lance un coucher de soleil obtient `264` s'il enchaîne, `265` s'il attend. Une table de valeurs
doit donc porter les deux, ou aucune. Sondes : `prealable_bit0.py`, `veille_bit0.py`,
`persistance_bit0.py`, `valide_correctif.py`.

**Le correctif a été vérifié dans `pysomneo`, pas seulement sur l'appareil.** La branche
`ai-improvements` a été exécutée avec l'ancienne table puis la nouvelle, sur le même coucher de
soleil réel :

| Table | `wusts` lu | `somneo_status` publié |
| --- | --- | --- |
| d'origine | 265 | **`unknown`** |
| corrigée (`2` renommé, `264` et `265` ajoutés) | 265 | **`sunset`** |

C'est la seule mesure qui exerce la bibliothèque plutôt que l'appareil, et elle a d'abord servi à
**invalider** une première version du correctif qui n'ajoutait que `264` : elle laissait
`somneo_status` à `unknown`. Ne rien proposer en amont sans l'avoir passée.

**Conséquence directe, et elle est lourde** : compléter la table de `pysomneo` en y ajoutant
`264` ne suffit pas, puisque le même geste produit `265` quelques heures plus tard. C'est la
démonstration la plus forte que le champ ne se lit pas par une table de valeurs — et elle
s'applique d'abord au correctif qu'on s'apprêtait à proposer. Relevé : `comparer-voies-*.json`,
sonde `probes/comparer_voies.py`.

**Ce que `pysomneo` en fait, mesuré le 2026-09-09 sur la branche `ai-improvements`** : avec la
table d'origine comme avec la table corrigée, `somneo_status` vaut `unknown` pour un coucher de
soleil réel. Le correctif envisagé n'aurait donc rien changé ce soir-là. C'est la seule mesure
qui ait exercé la bibliothèque elle-même plutôt que l'appareil — `probes/valide_fix.py`.

**Ce que le bit 9 signifie, vérifié à l'oreille le 2026-09-09.** Il marque une **source audio
engagée** — il se lève sur `snddv: "aux"` alors que rien n'est branché, donc sans qu'aucune
émission ne soit possible. Mais quand la source produit du son et que le volume suffit, le son
sort bel et bien : à `sdvol`/`sndlv` = 1 le propriétaire, présent dans la pièce, n'entendait
rien ; à 12, il a décrit sans voir le journal « trois bruits blancs » (les trois salves de radio
FM) puis « un bruit de pluie » (le thème *Soft Rain* du coucher de soleil). Les deux lectures
sont donc vraies à la fois, et il ne faut pas confondre le bit avec l'audibilité.

**Le modèle a été validé de l'extérieur** au même moment : le propriétaire a rapporté « la
lumière s'est allumée six fois, sans son », puis les quatre sons. Le bit 8 s'était levé six
fois et le bit 9 quatre fois. Un observateur qui ne voyait pas les relevés a décrit exactement
la séquence que les bits prédisaient.

**Les états relevés sur l'appareil, le 2026-09-06 — puis rejoués le 2026-09-09, et l'un des
trois ne s'est pas reproduit.** La première campagne (`probes/etats_et_udp.py`) enchaînait les
trois états **sans restaurer entre eux**, et ne relisait pas `wulgt` après écriture : elle ne
pouvait pas voir qu'un de ses relevés dépendait du contexte. La seconde
(`probes/etats_wusts.py`, trois répétitions avec retour au repos vérifié) et la sonde
d'arbitrage (`probes/veilleuse.py`) corrigent les deux défauts.

| État provoqué | `wusts` | Bits | Reproduit le 09 | Dans la table de `pysomneo` ? |
| --- | --- | --- | --- | --- |
| Repos | 1 | 0 | — | oui → `off` |
| Lampe allumée, **depuis le repos** | 257 | 0, 8 | 3/3 + 4/4 en niveau | oui → `light-on` |
| Veilleuse seule, **depuis le repos** | 257 | 0, 8 | 3/3 | oui → `light-on` |
| **Lampe ou veilleuse, allumée pendant le transitoire** | **258** | 1, 8 | **6/6** | **non → `unknown`** |
| **Coucher de soleil, sans son** | **264** | 3, 8 | **6/6**, deux contextes | **non → `unknown`** |
| Coucher de soleil, **avec** son | 776 | 3, 8, 9 | 6/6, deux contextes | oui → `sunset` |
| **RelaxBreathing** | **320** | 6, 8 | **8/8**, deux contextes | **non → `unknown`** |
| **Lecteur audio** (aux ou FM) | **513** | 0, 9 | **6/6** | **non → `unknown`** |
| **Lecteur audio + lampe** | **769** | 0, 8, 9 | **2/2** | **non → `unknown`** |
| ~~Veilleuse = 258~~ | ~~258~~ | — | **infirmé** — voir ci-dessous | — |

**Le niveau de la lampe n'entre pas dans `wusts`** : `ltlvl` à 1, 3, 12 et 25 donne `257` à
chaque fois. Et **l'afficheur non plus** : `wusts {"dspon": true}` puis `false` laissent
`wusts` à `1`. L'annotation « bit 1 = menu utilisateur affiché », tirée du code de
l'application, n'est donc **pas** l'afficheur permanent — question ouverte depuis le 31 août,
close par la mesure du 2026-09-09.

**La veilleuse ne vaut pas 258 : elle vaut 257, comme la lampe.** Provoquée seule
(`wulgt {"ngtlt": true}`, lampe éteinte), elle donne `257` trois fois sur trois le 2026-09-09.
**`wusts` ne distingue pas la veilleuse de la lampe** — seul `wulgt.ngtlt` le fait. Le `258` du
06 venait de ce que la lampe était restée allumée, et la sonde ne le relisait pas.

**Et le bit 0/1 ne décrit pas la lumière du tout.** `veilleuse.py` a écrit **deux fois la même
chose** à deux secondes d'intervalle et obtenu deux valeurs :

| Séquence | Écriture | `wulgt` relu | `wusts` |
| --- | --- | --- | --- |
| A | `{"ngtlt": true}` | `onoff=False ngtlt=True` | **257** (bits 0, 8) |
| B | `{"onoff": true, "ltlvl": 3}` | `onoff=True ngtlt=False` | **257** (bits 0, 8) |
| C | `{"onoff": true, "ltlvl": 3}` — identique à B | `onoff=True ngtlt=False` | **258** (bits 1, 8) |

Ce qui distingue C de B n'est pas l'état lumineux, qui est le même : c'est que C écrit pendant
que l'appareil est encore dans l'état transitoire `2` laissé par l'extinction précédente. **Le
bit 0 et le bit 1 portent un contexte d'interface, pas l'éclairage.**

**La règle est déterministe, et se provoque à volonté** (`contexte_wusts.py`, puis
`wusts_exhaustif.py`) : `wusts` **hérite du bit de contexte de l'état dans lequel se trouve
l'appareil au moment de l'écriture**.

| Action | Écrite depuis le repos (`1`) | Écrite pendant le transitoire (`2`) |
| --- | --- | --- |
| Lampe | **257** — 3/3 | **258** — 3/3 |
| Veilleuse | **257** — 3/3 | **258** — 3/3 |
| Coucher de soleil (sans son) | **264** — 3/3 | **264** — 3/3 |
| Coucher de soleil (avec son) | **776** — 3/3 | **776** — 3/3 |

Les deux formes de lumière héritent ; le coucher de soleil **efface** le bit de contexte. Un
délai de 2 s contre 15 s après extinction suffit à basculer d'une colonne à l'autre — c'est
donc reproductible par n'importe qui, sans outillage.

**Conséquence pour qui lit `wusts` : une table de valeurs entières ne peut pas être complète.**
Le même état physique produit plusieurs valeurs selon ce qui précède. C'est l'argument de fond
contre la table de `pysomneo`, et il ne dépend d'aucune valeur particulière.

Ce qui reste démontré, et suffit : **un usage ordinaire du réveil — le coucher de soleil —
produit une valeur que la table de huit entrées ne couvre pas**, et `STATUS.get(...)` renvoie
alors `unknown`.

**`2` est un état transitoire d'extinction, et sa durée est déterministe.** Échantillonné à
0,5 s par `wusts_exhaustif.py`, il tient **7,46 s — trois fois de suite, à 10 ms près** (vu de
t+0,7 s à t+8,2 s après l'extinction). Il apparaît après extinction de la lampe et du coucher
de soleil, **jamais** après celle de la veilleuse (mesuré le 2026-09-10, voir ci-dessous). C'est pourquoi les captures échantillonnées à 30 s le manquaient
une fois sur deux. Ce qu'il *désigne* reste inconnu — la mesure dit quand il apparaît et
combien il dure, pas ce qu'affiche l'appareil.

**La veilleuse ne produit pas `2` — 0 extinction sur 18, le 2026-09-10.** L'affirmation
reposait jusque-là sur un seul essai (`veilleuse.py`, séquence A). `extinction_veilleuse.py` l'a
mise à l'épreuve : chaque essai part d'un repos vérifié, relit `wulgt` après l'allumage, puis lit
`wusts` toutes les ~0,7 s pendant 12 s après l'extinction — 10 à 11 lectures valides dans la
fenêtre de `2`.

| Condition | Extinctions suivies de `2` |
| --- | --- |
| Lampe (niveau 3), témoin, même séance | **5 / 5** — de `t+0` à 7,3-7,9 s |
| Veilleuse tenue 2 s, extinction des trois champs | 0 / 5 |
| Veilleuse tenue 15 s, puis 120 s | 0 / 4 |
| Veilleuse allumée pendant le `2` de la lampe (elle vaut alors `258`) | 0 / 3 |
| Veilleuse éteinte par `{"ngtlt": false}` seul | 0 / 3 |
| Veilleuse allumée et éteinte **au bouton de l'appareil** (tenues ~6 s, ~6 s, ~60 s) | 0 / 3 |

Le bouton agit sur `ngtlt`, comme l'écriture réseau : `wulgt` passe à `ngtlt: true` puis revient,
`onoff` reste `false`. Relevés : `probes/results/extinction-veilleuse-20260910T214825.jsonl` et
`extinction-veilleuse-manuel-20260910T221245.jsonl`. Les trois extinctions de veilleuse relevées
par la capture les 07, 08 et 09/09 (`mslux` ≈ 6) vont dans le même sens, sans rien prouver à 30 s
d'échantillonnage.

**Une alarme complète, observée sans y toucher — 2026-09-07.** Les états ci-dessus étaient
provoqués à la main. Celui-ci a été relevé au fil d'un vrai réveil, `wusts` interrogé toutes
les 30 s, sans intervention :

| Instant | `wusts` | Bits | Ce qui est **mesuré** en même temps | `pysomneo` |
| --- | --- | --- | --- | --- |
| `A − 34 min 50 s` | **2309** | 0, 2, 8, 11 | `wutim` démarre à 6 s ; `mslux` monte | `wake-up` |
| `A + 21 s` | **2817** | 0, 8, 9, 11 | `wutim` = 2 051 s ; `mslux` = 2 798 (lampe à fond) | `on` |
| `A + 51 s` | **2** | 1 | `wutim` retombe à **65 535** : la séquence est finie | **`sunset`** |
| `A + 1 min 22 s` | 1 | 0 | `mslux` = 117 : la lampe est éteinte | `off` |

Trois choses en sortent :

- **Le bit 9 accompagne la phase sonore** — appuyé, pas prouvé. La sonnerie le lève
  (2309 → 2817) et la table amont le corrobore (776 et 777, coucher de soleil *avec* son, le
  portent). Mais **personne n'a entendu l'alarme** : que 2817 corresponde au démarrage du son
  est déduit de l'horaire (35 min après l'aube) et du bit, pas d'une observation directe.
- **Le bit 2 retombe quand le son démarre** — observé sur **une seule** transition. L'aube
  porte le bit 2, la valeur suivante non. C'est cohérent avec `isAlarmActive()`, qui teste
  `bit2 == 1 || bit11 == 1` : le `|| bit11` rattraperait la phase sonore. Une alarme suffit à
  le montrer, pas à en faire une règle — et le **rappel** (snooze, `2321` dans la table amont)
  n'a jamais été observé en conditions réelles, l'alarme ayant été coupée d'emblée.
- **`2` est étiqueté `sunset` par la table amont, alors qu'il apparaît ici à l'arrêt d'une
  alarme.** C'est un défaut d'une autre nature que `unknown` : une étiquette *fausse* plutôt
  qu'absente. Le coucher de soleil provoqué et mesuré la veille vaut **264**, pas 2.

**Une deuxième alarme, le 2026-09-08** — même réveil, même heure, `wusts` toutes les 30 s, sans
intervention. Elle rejoue la première et la sépare de ce qui n'en était pas :

| Instant | `wusts` | Bits | Ce qui se passe |
| --- | --- | --- | --- |
| `A − 34 min 28 s` | **2309** | 0, 2, 8, 11 | l'aube démarre, `wutim` à 28 s |
| `A + 5 s` | **2817** | 0, 8, 9, 11 | phase sonore, `wutim` = 2 051 s |
| `A + 1 min 06 s` | **258** | 1, 8 | lumière, bit 1 — c'est l'appui sur le bouton d'arrêt |
| `A + 1 min 36 s` | **257** | 0, 8 | lumière allumée, `mslux` = 398 — elle le reste 14 min |
| `A + 15 min 21 s` | **2** | 1 | `mslux` retombe à 116 |
| `A + 15 min 52 s` | 1 | 0 | repos |

Deux points s'en trouvent renforcés, un troisième corrigé :

- **Le bit 2 retombe quand le son démarre** : deuxième observation de la transition
  2309 → 2817, dans les mêmes termes. Ce n'est plus un cas isolé.
- **`2` n'est pas un « coucher de soleil ».** Le 7, il suivait l'arrêt de l'alarme ; le 8, il
  suit l'extinction de la lampe de chevet, quatorze minutes plus tard et sans aucun rapport avec
  une alarme. Le point commun des deux relevés est ailleurs : **`2` précède `1` d'un seul
  échantillon**, à chaque fois. C'est un état de passage vers le repos, pas un mode ; l'étiquette
  `sunset` de la table amont est fausse dans les deux cas.
- **La sortie d'alarme n'a pas de forme unique.** Le 7 : 2817 → 2 → 1 en une minute. Le 8 :
  2817 → 258 → 257 → 2 → 1 en quinze — et le `258` s'y lit désormais comme « lumière allumée
  avec le bit de contexte levé », non comme la veilleuse. Un client qui attendrait une séquence fixe pour détecter
  la fin d'un réveil se tromperait un matin sur deux — ce qui suit l'alarme, c'est ce que
  l'utilisateur fait, pas ce que l'appareil décide. Les quatorze minutes de lumière du 8 le
  disent bien : le propriétaire l'a allumée **par inadvertance** en arrêtant la sonnerie, puis
  éteinte (confirmé le 2026-09-08). Rien dans ces bits ne distingue une intention d'un geste
  de travers.

**Deux alarmes de plus, les 2026-09-09 et 2026-09-10** — même capture, `wusts` toutes les 30 s,
sans intervention :

| Matin | Sortie d'alarme | Premier relevé de `wungt` avec `night: false` |
| --- | --- | --- |
| 09 | `2817` (`A + 16 s`) → `2` (`A + 47 s`) → `1` (`A + 1 min 17 s`) | `A + 21 s` |
| 10 | `2817` (`A + 3 s`, `A + 34 s`) → **lecture échouée** (`A + 1 min 04 s`) → `1` (`A + 1 min 35 s`) | `A + 40 s` |

- **Le rappel n'apparaît toujours pas.** Le 10, le propriétaire pensait avoir appuyé sur le
  rappel. Le relevé ne le montre pas : après `A + 1 min 35 s`, `wusts` vaut `1` sans
  interruption jusqu'au soir, et **aucune seconde sonnerie** ne se produit (`snztm` vaut 8).
  Un rappel laissé courir aurait refait sonner l'alarme. Restent deux lectures, que ce relevé ne
  départage pas : l'arrêt a été pressé, ou un rappel a été annulé en moins de trente secondes —
  dans la fenêtre que la lecture échouée laisse aveugle. **`2321` reste non observé après quatre
  alarmes.**
- **La lecture échouée tombe au moment de l'appui**, en `SSL: UNEXPECTED_EOF_WHILE_READING` —
  la signature d'une connexion concurrente (§7). Une seule occurrence sur quatre matins : notée,
  pas généralisée.
- **La clôture de la nuit par le firmware à l'heure programmée se confirme**, les 09 et 10 comme
  le 08 : `tendb` y vaut `A`, et `wungt` passe à `night: false` dans la minute qui suit `A`,
  avant tout geste.

  > **Ce qu'on sait, et ce qu'on ne sait pas — à tenir séparé.** `2` est un **état transitoire
  > d'extinction de 7,46 s**, mesuré le 2026-09-09 (§ modèle en bits) sur douze extinctions,
  > dont trois échantillonnées à 0,5 s. Il ne suit jamais l'extinction de la veilleuse (0 sur
  > 18, 2026-09-10). S'y
  > ajoutent quatre observations fortuites, toujours juste avant le retour à `1` : la
  > restauration du 06, les alarmes des 07 et 09, l'extinction de lampe du 08.
  >
  > **Ne pas écrire que `2` est « l'afficheur allumé »** — affirmé ici par erreur le
  > 2026-09-07 : `dspon` vaut `False` sur le relevé même. Ce qu'il *désigne* reste inconnu ;
  > la mesure dit quand il apparaît et combien il dure, rien de plus.
  >
  > L'argument à porter en amont est donc le plus étroit : **le coucher de soleil de cet
  > appareil vaut 264, et `2` est un transitoire d'extinction.** Cela suffit à montrer que la
  > table associe `sunset` à une valeur qui n'est pas celle du coucher de soleil ici, sans
  > prétendre savoir ce que `2` désigne.

Deux corrections à apporter aux hypothèses du 31 août :

- **bit 3 = coucher de soleil : confirmé** par la mesure (264 = bits 3 et 8). Ce n'était qu'une
  déduction depuis la table de `pysomneo`.
- **bit 1 : l'annotation « menu utilisateur affiché » n'est ni confirmée ni infirmée.** Elle
  avait été mise en doute ici le 2026-09-07 au motif qu'il « apparaît avec la veilleuse » —
  **c'était faux**, et la mesure du 2026-09-09 l'a corrigé : la veilleuse seule vaut `257`,
  bit 0. Ce qui est établi, c'est que les bits 0 et 1 s'échangent selon le contexte antérieur
  et non selon l'éclairage. Ne rien affirmer de plus.
- **bit 8 accompagne toute émission de lumière** — lampe, veilleuse et coucher de soleil le
  portent tous les trois.

Les autres champs du port se lisent tels quels : `snztm`, `nrcur`, `pwrsz`, `fmrna`,
`wutim`/`dutim`/`sntim` (**`65535` = inactif**), `rpair`, `hmlay`.

> **`wutim` compte des secondes écoulées, pas des minutes restantes.** Relevé pendant l'alarme
> du 2026-09-07 : le champ passe de `6` à `2051` en trente-cinq minutes, par pas d'environ 30
> — soit exactement le pas d'interrogation. Il **croît** depuis le début de la séquence, il ne
> décompte pas, et il retombe à `65535` dès l'arrêt. La lecture « minutes restantes », héritée
> du nom du champ, était fausse ; c'est un chronomètre de la séquence en cours, en secondes.
> (L'horloge interne est légèrement lente : 2 045 s comptées pour 2 111 s réelles, soit 3 %.)
>
> À ne pas confondre avec le **port** `wutim`, l'horloge de l'appareil, qui n'a rien à voir.

**Réglages d'afficheur** — `brght` et `dspon` sont sur ce même port et sont **écrivables** :

```
PUT wusts {"dspon": true, "brght": 4}   # allumé, intensité 4
PUT wusts {"dspon": false}              # afficheur éteint
```

`brght` est borné à **1–6** — et ce n'est pas seulement une limite de l'application
constructeur (`isWithinLimit()`), **c'est l'appareil qui l'impose**. Vérifié par écriture le
6 septembre 2026 sur le HF3671/01, firmware `swverwifi` 2.2.5, avec relecture après chaque
essai et restauration de la valeur initiale :

| Valeur écrite | Réponse | Valeur relue |
| --- | --- | --- |
| 1 | `200` | 1 |
| 6 | `200` | 6 |
| 0 | **`422`** | inchangée |
| 7 | **`422`** | inchangée |
| 128 | **`422`** | inchangée |

**Le bug rapporté sur cette même issue #13, reproduit puis daté — 2026-09-07.** Nezz signalait
le 18 juin 2024 que régler la luminosité puis l'affichage-toujours-allumé depuis une même
automation Home Assistant fait perdre le premier réglage, et qu'un délai de deux secondes
corrige. L'explication naturelle serait la concurrence de l'issue #8. **Elle est fausse** : la
cause est un cache client, et elle se lit dans le code de l'époque.

`set_display` envoie **toujours les deux champs** — celui qu'on lui passe, et l'autre repris de
`self.data`. Or en juin 2024, `self.data` n'était pas rafraîchi après le `PUT` :

```python
payload['dspon'] = state if state != None else self.data['display_always_on']
payload['brght'] = brightness if brightness else self.data['display_brightness']
self.alarm_status = self._put('wusts', payload = payload)   # self.data jamais mis à jour
```

Le second appel réinjecte donc la valeur d'**avant** le premier, et l'annule. Vérifié sur
l'appareil (`probes/repro_nezz.py`), état initial `dspon=False, brght=1`, cible `brght=4` :

| Séquence | `PUT` envoyé | État relu |
| --- | --- | --- |
| `set_display(brightness=4)` | `{"dspon": false, "brght": 4}` | `brght=4` ✅ |
| `set_display(state=True)` | `{"dspon": true, "brght": **1**}` | `brght=1` ❌ **perdu** |

Le délai de deux secondes marchait parce que le coordinator de Home Assistant avait le temps de
rafraîchir le cache entre les deux appels — pas parce qu'il désengorgeait quoi que ce soit.

**C'est corrigé depuis le commit `3fc2c33` (20 septembre 2025)** — « Put commands don't return
the full internal state, therefore we need another api get request to update the internal
state ». Il remplace `self._update_alarm_status()` par `self._fetch_alarm_status()` : au lieu de
recharger le cache depuis la réponse du `PUT`, on relit l'appareil. Rejouée contre `pysomneo`
5.0.6, la séquence de Nezz tient : `brght` reste à 4.

> **Ne pas citer `6b99ce4` comme le correctif** — erreur commise ici le 2026-09-07 avant
> vérification. Ce commit du 22 septembre 2025 déplace `pysomneo/__init__.py` vers
> `pysomneo/somneo.py` : le `_fetch_alarm_status()` y apparaît en `+` parce que **le fichier
> entier est nouveau**, pas parce que le comportement change. Un `git log -S` restreint au
> fichier d'arrivée ne voit que ce déplacement ; il faut interroger les deux chemins.

Le cache a connu **trois** états successifs, et les deux premiers pouvaient produire le symptôme :

| Période | Après le `PUT` | Le cache est-il juste ? |
| --- | --- | --- |
| Jusqu'à ~2025 | rien | **Non** — jamais rafraîchi, c'est le code que voyait Nezz en juin 2024 |
| Jusqu'au 2025-09-20 | `_update_alarm_status()` | **Non** — rechargé depuis la réponse du `PUT`, que le mainteneur décrit lui-même comme incomplète |
| Depuis `3fc2c33` | `_fetch_alarm_status()` | Oui — l'appareil est relu |

C'est la réponse à l'issue #13 de `pysomneo`, ouverte depuis mai 2023 : la bibliothèque
implémente déjà `set_display()`, mais sa docstring annonce `brightness: 0-255` et rien ne
valide. Un appelant qui suit la documentation reçoit un `422`. Contrairement à `ltlvl`
(lumière), que la bibliothèque met bien à l'échelle sur 0–255, `brght` est transmis tel quel :
la docstring ne décrit donc aucune conversion, elle est simplement fausse.

**`wudsk`** (coucher de soleil) : `durat`, `onoff`, `curve`, `ctype`, `sndtp`, `snddv`
(`dus`/`fmr`/`off`), `sndch`, `sndlv`, `sndss`.
**`wurlx`** (RelaxBreathe) : `durat`, `onoff`, `progr` (1-7), `rlbpm` (rythmes disponibles),
`pause`, `intny`, `rtype`, `sndlv`.
**`wulgt`** : `ltlvl` (0-25), `onoff`, `ngtlt` (veilleuse), `ctype`, `tempy`, `diman`, `pwmon`.
**`wufmp/00`** : dictionnaire `"1".."5"` → fréquence en MHz.

## 5. Ce que l'application fait via le cloud — le point qui change le projet

Le port `dataupload` du produit 1 décrit une collecte **poussée vers Philips** :

```json
{"contractid":"SRC.BrightEyes.Device.Data","uploadperiod":2880,"uploadnow":false,
 "event.1":{"sampleperiod":0},   "temp.1":{"sampleperiod":900},
 "hum.1":{"sampleperiod":900},   "snd.1":{"sampleperiod":900},
 "lux.1":{"sampleperiod":900}}
```

Le réveil échantillonne température, humidité, bruit et lumière **toutes les 900 s (15 min)**
et les téléverse par lots (`uploadperiod` 2880 — unité non confirmée). `device.allowuploads`
vaut `true`. Le port `backend` donne la destination et prouve que la liaison est vivante :

```json
{"url":"http://www.ecdinterface.philips.com/DevicePortalICPRequestHandler/RequestHandler.ashx",
 "dcs-state":"subscribed","clientversion":"DCDeviceClient_1.9.0.5","dcsenabled":true}
```

Côté application, les graphiques d'historique ne sont **jamais** construits à partir de
l'API locale : ils viennent de `com.philips.platform.core.datatypes.Moment`, la couche de
synchronisation HealthSuite, sous les types `sleepRoomSession`,
`sleepRoomTemperatureAggregate`, `sleepRoomHumidityAggregate`,
`sleepRoomIlluminationAggregate` et `sleepRoomSoundAggregate`.

### Ce que le réveil garde en local avant de téléverser

Les sous-ressources `dataupload/{temp,hum,snd,lux}.1/data` **ne sont pas vides** : elles
exposent la fenêtre d'agrégation en cours, celle qui sera envoyée au prochain lot. C'est
strictement plus riche que `wusrd`, et c'est ce qui alimente le `SensorReading(moyenne, min,
max, période)` de l'application.

```json
temp.1/data → {"svper":898,"avtmp":26.3,"lotmp":26.3,"hitmp":26.4}
hum.1/data  → {"svper":898,"avhum":56.2,"lohum":56.1,"hihum":56.4}
snd.1/data  → {"svper":898,"avsnd":39,"losnd":26,"hisnd":70,
               "absnd":[5,[0,30,11484],[30,40,35970],[0,0,0],[40,3276,42256],[0,0,0]],
               "rlsnd":[5,[-3276,3,85589],[3,6,2225],[-6,-3,15],[6,3276,1676],[-3276,-6,7]]}
lux.1/data  → {"svper":898,"avlux":8.4,"lolux":0.0,"hilux":114.8,"ablux":[…],"rllux":[…]}
```

- `svper` : **constante, toujours 898.** Ce n'est *pas* un compteur de progression — voir
  ci-dessous, c'est le piège de cette section.
- `av*` / `lo*` / `hi*` : **moyenne, minimum, maximum** sur la fenêtre.
- `ab*` / `rl*` (bruit et lumière seulement) : **histogrammes**, `[n, [borne_basse, borne_haute,
  compte] × n]` — `ab*` sur les valeurs absolues, `rl*` sur les variations. Le relevé ci-dessus
  se lit : 11 484 échantillons entre 0 et 30 dB, 35 970 entre 30 et 40, 42 256 au-dessus de 40.

> **`svper` ne dit pas où on en est dans la fenêtre — mesuré le 2026-09-07.** La lecture
> « secondes écoulées, plafond 900 » était une inférence de nom, et elle est fausse : sur
> **391 relevés couvrant 22 heures**, `svper` a valu **898, sans exception et sans jamais
> varier**. C'est la durée nominale de la fenêtre (900 s moins deux), une constante de
> configuration — pas un compteur.
>
> Il n'existe donc **aucun champ qui annonce une bascule de fenêtre**. Ce que montre la mesure :
> le triplet `(av, lo, hi)` reste figé une quinzaine de minutes, puis est remplacé d'un bloc.
> La bascule ne se déduit que d'une comparaison avec le relevé précédent — et **cette
> comparaison échoue quand deux fenêtres consécutives portent les mêmes valeurs**, ce qui
> arrive toutes les nuits : `lolux = hilux = avlux = 0.0` de 23 h 32 à 06 h 28, soit 28 fenêtres
> indiscernables les unes des autres.
>
> Deux conséquences pour le collecteur :
>
> 1. **Interroger à pas fixe et horodater soi-même**, sans chercher à reconstituer les
>    frontières de fenêtre depuis l'appareil : il ne les donne pas.
> 2. **L'agrégat publié est en retard d'une fenêtre.** L'extinction du plafonnier de 23 h 09
>    n'apparaît dans `lolux`/`hilux` qu'au relevé de 23 h 17 : c'est la fenêtre *close* qui est
>    servie, pas celle en cours. Un extremum peut donc avoir jusqu'à 15 min de retard sur
>    l'événement qui l'a produit — à ne jamais horodater à l'heure de lecture.

**C'est directement exploitable par le scraper**, et supérieur à un simple `wusrd` échantillonné :
un pic de bruit à 70 dB survenu entre deux interrogations est perdu par `wusrd`, mais reste
dans `hisnd`. La bonne stratégie de collecte est donc de lire **`wusrd` pour l'instantané et
`dataupload/*/data` pour les extrema de la fenêtre** — cela capture les pics sans avoir à
interroger l'appareil à haute fréquence, ce que son tas ne supporterait pas.

**Conséquences, à intégrer à l'architecture :**

1. **L'API locale n'a aucune mémoire longue.** Aucun endpoint d'historique n'existe : ce qui
   est accessible se limite à l'instant (`wusrd`), à la fenêtre de 15 min en cours
   (`dataupload/*/data`) et à la nuit en cours (`wungt`). Rien n'est conservé au-delà : toutes
   les variantes d'historique testées (`wuhis`, `wusrd/history`, `wungt/history`, `wudta`,
   `wulog`…) répondent `422`. Dès que la fenêtre bascule, ce qui n'a pas été relevé est perdu.

   > **`dataupload/event.1/data` ne fait pas exception — vérifié le 2026-09-07.** Découvert le
   > jour même, c'est le seul port qui ait renvoyé quelque chose ressemblant à un événement
   > daté, et il valait la peine d'y regarder : un journal d'événements aurait permis de dater
   > une alarme sans interroger `wusts` en continu.
   >
   > ```
   > 17:59:15  {"event":"endalarm", "stime":"2026-09-07T17:59:12+02:00", "pname":""}
   > 18:00:18  {"event":"endalarm", "stime":"2026-09-07T18:00:15+02:00", "pname":""}
   > 18:02:22  {"event":"endalarm", "stime":"2026-09-07T18:02:19+02:00", "pname":""}
   > ```
   >
   > **`stime` est l'heure de la requête, pas celle de l'événement** — il suit l'horloge à
   > trois secondes près à chaque lecture, et l'alarme en question s'était terminée onze heures
   > plus tôt. Le port ne retient que le **type** du dernier événement, sans date, dans le même
   > moule que les capteurs (`tzhrs`, `tzmin`, `shdst`). `event.2` et `event/data` répondent
   > `422` : il n'y a qu'un seul canal, et il n'a pas de profondeur.
   >
   > Le collecteur ne peut donc dater une alarme qu'en observant `wusts` — c'est la troisième
   > variante d'historique local testée, et le troisième verdict identique.
2. **Couper internet supprime donc l'intégralité de l'historique de SleepMapper.** Ce n'est
   pas un effet de bord : c'est la fonction principale de l'app qui disparaît.
3. `Somneo-Scraper` n'est donc pas un simple cache d'accélération — il est la **seule**
   source d'historique possible après isolement. Sa base SQLite devient la mémoire du réveil.
4. En échantillonnant plus vite que 15 min, le projet fait **mieux** que l'original.
5. L'app conserve un intérêt de référence : le blocage doit viser
   `www.ecdinterface.philips.com`, et l'on peut vérifier l'isolement en relisant
   `backend.lastsignon` et `transport.state`.

## 6. Notifications push — l'alternative à l'interrogation

L'appareil sait **notifier** au lieu d'être interrogé, et l'application s'en sert. Le
mécanisme, lisible dans `SubscribeRequest` :

```
POST /di/v1/products/{n}/{port}
{"subscriber": "<identifiant client>", "ttl": <secondes>, "changeudp": <port UDP>}
```

L'appareil renvoie ensuite un **datagramme UDP** vers ce port (8080 par défaut) à chaque
changement d'état du port souscrit ; côté client, `LocalSubscriptionHandler` ouvre le socket
et reçoit les événements. Un en-tête `X-Condor-Features: changeindication-port` négocie la
variante avec port personnalisé.

Que les abonnements sont bien tenus côté appareil se vérifie dans
`GET /di/v1/products/0/sub`, qui liste les souscriptions actives avec leur `ttl`.

**Intérêt direct pour le projet :** sur un appareil qui tombe en `500 Timeout` sous une
rafale, remplacer une partie de l'interrogation par des abonnements est le bon levier —
notamment pour les états qui changent rarement mais qu'on veut voir tout de suite (alarme
déclenchée, snooze, début de nuit dans `wungt`). Les capteurs, eux, restent à interroger.

> **Première tentative, le 2026-09-06 — sans succès, et la forme employée est en doute.**
> `POST wulgt {"subscriber": "...", "ttl": 120, "changeudp": 9999}`, avec l'en-tête
> `X-Condor-Features`, renvoie **`200` accompagné du corps du port** — pas d'accusé
> d'abonnement. Aucun datagramme n'est arrivé sur le port UDP pendant les dix secondes
> suivantes, alors que trois changements d'état étaient provoqués sur ce port même.
>
> Le port `sub` du produit 0 liste pourtant des abonnements bien réels — pour `firmware` et
> pour `1/wifiui` — avec un `ttl` de 9 223 371 272, c'est-à-dire une valeur qui n'expire pas.
> Aucun n'a été créé par nous. Conclusion prudente : **le mécanisme existe, notre requête n'est
> pas la bonne.**

> **Trois autres formes essayées le 2026-09-07 — toutes en échec, et le mécanisme reste fermé.**
> Après chaque tentative, `GET products/0/sub` a été relu pour voir si l'abonnement avait pris :
>
> | Forme (`POST`) | Réponse | Abonnement créé ? |
> | --- | --- | --- |
> | `wusrd` + `changeudp`, sans en-tête Condor | `connection refused` (TCP) | non |
> | `wusrd` + `changeudp`, avec en-tête Condor | `connection refused` (TCP) | non |
> | `wusrd` **sans** `changeudp` | `200` — corps du port | **non** |
> | `products/0/sub` + `changeudp` | `200` — liste des abonnements | **non** |
>
> **Aucune des quatre formes n'a créé quoi que ce soit** : `sub` n'a jamais mentionné le
> `subscriber` déclaré. Le `200` de la troisième forme est trompeur — l'appareil renvoie le
> corps du port, exactement comme un `GET`, sans rien enregistrer.
>
> **Ce qui reste non tranché, et qu'il faut noter comme tel.** Les deux formes portant
> `changeudp` sont tombées au niveau TCP, les deux sans en ont réchappé — ce qui ferait
> soupçonner ce champ. Mais le volet des méthodes HTTP montre que `POST` et `PATCH` sont
> *déjà* refusés en TCP sur ce même port, sans aucun corps. La corrélation est donc réelle,
> la causalité non établie : il n'y a pas eu de contre-épreuve isolant le champ. **Ne pas
> écrire que `changeudp` fait tomber la connexion** — c'est une hypothèse, pas une mesure.
>
> Un fait annexe, en revanche, est acquis : la liste des abonnements comporte désormais une
> **troisième** entrée, sur le port `fac`, absente du relevé du 6 septembre. Ces abonnements
> apparaissent et disparaissent sans que nous y soyons pour rien — ils sont le fait du firmware.

**État de la question, au 2026-09-07 : l'abonnement UDP n'est pas exploitable.** Sept formes
essayées sur deux jours, aucune n'a créé d'abonnement. La collecte repose donc entièrement sur
l'interrogation — ce qui est sans conséquence pratique depuis que la cadence de 5 s est mesurée
comme tenable (§7). L'intérêt de l'abonnement était d'épargner l'appareil ; cette économie
n'est plus nécessaire.

## 7. Pièges relevés

- **Le tas a un plancher stable, ce n'est pas une pénurie.** Mesuré le 6 septembre 2026 sur
  l'appareil en service : `heap_free` vaut **33 024 octets au repos**, descend sous charge
  jusqu'à **24 824** et **s'y tient exactement**, sans bouger d'un octet, pendant 1 h 45 de
  sondage continu — sans une seule erreur. Le « ~25 ko » relevé le 31 août est donc le
  **plancher sous charge**, pas la mémoire disponible de l'appareil.
- **Ce qui casse l'appareil, c'est la concurrence — pas le débit, pas la mémoire.** Mesuré le
  6 septembre 2026, 21 requêtes par condition, à charge égale :

  | Condition | Réussite | Latence médiane |
  | --- | --- | --- |
  | Sérialisé, connexion neuve à chaque fois | **21/21** | 516 ms |
  | Sérialisé, **connexion réutilisée** (keep-alive) | **21/21** | **36 ms** |
  | 3 requêtes en vol, connexions neuves | 11/21 | 511 ms |
  | 3 requêtes en vol, connexions réutilisées | 7/21 | 388 ms |
  | 7 requêtes en vol | 8/21 | 471 ms |

  En sérialisé, **vingt `GET` enchaînés sans aucune pause passent tous**, et le tas ne bouge
  pas d'un octet pendant ce temps : ce n'est pas une pénurie de mémoire.

  **La marche est à deux connexions.** Mesurée le 6 septembre 2026, trois séries par palier,
  connexions réutilisées :

  | Requêtes en vol | Réussite | Erreur dominante |
  | --- | --- | --- |
  | 1 | **63/63** sur trois séries | — |
  | 2 | **33/60** — 11/20 trois fois de suite | `RemoteDisconnected`, 9 par série |
  | 3 | **21/63** — 6, 8 et 7 | `RemoteDisconnected` et `SSLEOFError` |

  Le taux de réussite suit **1/N** : 100 %, 55 %, 33,3 % — ce que produit un serveur qui n'en
  sert qu'une et laisse tomber les autres. (Un palier à 7 mesuré une seule fois, avec un autre
  protocole, donne 38 % et ne s'y range pas : série unique, non retenue.)

  **L'appareil ne sert qu'une connexion TLS à la fois**, et ce n'est pas une déduction isolée :
  le rapporteur de l'issue #8 l'avait observé dès le 27 décembre 2022 — « si je me connecte
  depuis deux terminaux, la première connexion est éjectée quand la seconde arrive » — avec la
  même erreur `unexpected eof while reading`. Deux observations indépendantes, à quatre ans
  d'écart, sur deux appareils différents.
- **C'est la requête déjà en vol qui tombe, pas la nouvelle.** Motif réel mesuré le
  2026-09-06 : un fil enchaîne sept lectures comme le fait un rafraîchissement complet, un
  second envoie **une** requête à un instant tiré au hasard dans cette fenêtre. Résultat sur
  vingt essais — la requête isolée réussit **20/20**, le rafraîchissement perd 12 lectures sur
  140, et **la moitié des rafraîchissements (10/20) est abîmée**. Sans concurrence, le même
  rafraîchissement passe **70/70**. L'appareil sert donc la connexion arrivante et laisse
  tomber la précédente.
- **Réutiliser la connexion est un gain, pas un risque.** L'écart entre connexion neuve et
  connexion réutilisée est de ~480 ms, soit 93 % du temps d'une requête — établissement TCP et
  poignée de main TLS confondus, la part de chacun n'ayant pas été isolée. En keep-alive, une
  lecture retombe à **36 ms**. C'est ce qui explique que le rapporteur ait vu ses timeouts
  disparaître en **activant** la réutilisation TLS, en mars 2024, sans comprendre pourquoi :
  elle supprime la seconde connexion.
- **La réécriture asynchrone, mesurée contre l'appareil le 2026-09-07** — ce n'est plus une
  inférence. La branche `ai-improvements` (6.0.0b0) monte `aiohttp.TCPConnector(ssl=False)`
  **sans `limit`** ; les défauts d'aiohttp étant `limit=100` et `limit_per_host=0`, elle
  autorise **cent** connexions simultanées là où `master` en autorise cinq. La protection
  accidentelle qu'offrait l'API synchrone — un fil, un appel à la fois — disparaît avec
  `asyncio.gather`, qui est la façon normale d'écrire de l'async.

  N appels à `fetch_data(force_slow_refresh=True)` en `gather` sur une instance partagée,
  trois séries par palier (`probes/async_pool.py`) :

  | Connecteur | 2 tâches | 3 tâches | 6 tâches |
  | --- | --- | --- | --- |
  | `TCPConnector(ssl=False)` — l'existant | 6/6, 9,8 s | 9/9, 17,1 s | **16/18**, 22,1 s |
  | `+ limit=1` | 6/6, **0,9 s** | 9/9, **1,4 s** | **18/18, 2,8 s** |
  | `+ limit=1, force_close=True` | **5/6**, 14,2 s | 9/9, 24,5 s | **9/18**, 24,4 s |

  Deux enseignements, et le second était inattendu :

  1. **`limit=1` suffit, et l'écart est massif** : facteur onze sur le temps à 2 et 3 tâches,
     et surtout **plus aucune perte** à 6 tâches là où la branche en perd deux.
  2. **Ne pas y ajouter `force_close=True`.** Fermer la connexion après chaque requête détruit
     la réutilisation TLS — celle qui fait passer une lecture de 516 ms à 36 ms — et fait
     perdre **la moitié** des appels à 6 tâches. C'est le correctif qu'on serait tenté
     d'écrire « pour être sûr » ; il est deux fois pire que le mal.

  Le correctif est donc `TCPConnector(ssl=False, limit=1)` : sérialiser sans jamais refermer.

> **Piège de mesure, pour qui voudrait rejouer ceci.** Substituer l'attribut `_session` de
> `SomneoSession` ne suffit pas : sur une erreur de connexion, la branche appelle
> `_reset_session()`, et `_get_session()` recrée alors une session avec **son** connecteur.
> La contrainte testée disparaîtrait dès la première erreur — c'est-à-dire exactement quand
> elle compte, et sans que rien ne le signale. C'est la **méthode** `_get_session` qu'il faut
> remplacer.
- **L'espacement de 200 ms reste une prudence raisonnable**, mais ce n'est pas lui qui évite
  les échecs : c'est le fait de n'avoir qu'une requête en vol.
- **La fréquence n'est pas une contrainte — jusqu'à une lecture toutes les 5 secondes.**
  Mesuré le 2026-09-07, quatre paliers de 45 min chacun, sérialisés
  (`probes/cadence.py`) :

  | Période | Requêtes | Échecs | Médiane | Max | Tas libre (début → fin) |
  | --- | --- | --- | --- | --- | --- |
  | 60 s | 90 | **0** | 528 ms | 1 618 ms | 24 960 → 24 960 |
  | 30 s | 180 | **0** | 532 ms | 1 032 ms | 24 960 → 24 960 |
  | 15 s | 360 | **0** | 528 ms | 1 208 ms | 24 960 → 24 960 |
  | **5 s** | **1 080** | **0** | 528 ms | 1 219 ms | 24 960 → 24 960 |

  Trois heures, 1 710 requêtes, **pas un seul échec**, et un tas qui ne bouge pas d'un octet
  entre le premier et le dernier palier. La latence est plate : douze fois plus de requêtes ne
  coûtent pas une milliseconde de plus. Rien n'indique que 5 s soit une limite — c'est le
  palier le plus rapide essayé, pas celui où quelque chose a cédé.

- **La contre-épreuve est venue toute seule, le même jour.** Sur les deux journées de capture,
  **88 échecs** — et leur répartition tranche : les 2 221 relevés de la nuit, de 23 h 09 à
  06 h 15 sans concurrence, en comptent **zéro**, tandis que 79 des 88 tombent dans les
  23 minutes où deux clients ont interrogé l'appareil en même temps (un superviseur ayant
  relancé la capture par-dessus une sonde). L'erreur y est toujours la même :
  `SSL: UNEXPECTED_EOF_WHILE_READING`.

  Sept heures d'affilée sans une erreur, puis un taux d'échec massif dès qu'un second client
  arrive : **la seule variable qui compte est le nombre de connexions, jamais la cadence.**
- **Le pool de connexions de `pysomneo` est la cause qui reste — mesuré le 2026-09-07.**
  La branche master monte son adaptateur avec `pool_connections=5`, `pool_maxsize=5` et
  `pool_block=False` : jusqu'à cinq connexions simultanées vers un appareil qui n'en sert
  qu'**une**, et rien ne borne le parallélisme quand le pool est saturé. Le correctif de mai
  2026 (« Improve timeout resilience and connection pooling ») a ajouté réessais, backoff et
  réinitialisation du pool — il traite le symptôme, la configuration laisse la cause en place.

  Trois fils lisant sept ports chacun, trois séries par condition (`probes/pool_serialise.py`) :

  | Condition | Réussite | Durées des 3 séries | Pire requête |
  | --- | --- | --- | --- |
  | `pool=5, block=False` (l'existant) | 63/63 | 16,0 · 16,1 · 17,8 s | **17 552 ms** |
  | `pool=1, block=True` | 63/63 | 1,4 · 3,3 · 3,6 s | 2 645 ms |
  | `pool=1, block=True`, sans réessai | 63/63 | 1,4 · 1,6 · 1,9 s | 936 ms |

  **Le taux de réussite ne bouge pas — c'est le temps qui explose**, d'un facteur dix, de façon
  reproductible au dixième de seconde. Les réessais rattrapent les connexions éjectées, si bien
  que rien n'apparaît dans un compte d'erreurs ; ce que l'appelant voit, lui, c'est une lecture
  qui dure. **Et la pire requête atteint 17,5 s pour un `timeout` par défaut de 20 s** : c'est
  exactement le `ReadTimeout: read timeout=20` de l'issue #8, à 2,5 s près. Un fil de plus, ou
  un réseau un peu plus lent, et la limite est franchie.

  Borner le pool à une connexion **bloquante** laisse urllib3 sérialiser de lui-même : la
  seconde requête attend la libération au lieu d'ouvrir une connexion que l'appareil éjectera.
  La latence médiane retombe de 1 032 ms à 116 ms — la réutilisation TLS redevient effective.
  C'est aussi ce qui explique le « voodoo magic » du rapporteur en mars 2024 : activer la
  réutilisation TLS supprimait la seconde connexion.

  L'écart entre les deux dernières lignes se lit aussi : **les réessais coûtent encore 1,7 s
  alors qu'aucune erreur n'est signalée** — ils se déclenchent donc sur des échecs transitoires
  que le bilan ne montre pas.
- **L'index `/di/v1/products/1/` expire toujours en `500`** — lui seul, de façon reproductible.
  C'est le seul échec constaté à ce jour.
- **`/di/v1/products/0/` fonctionne, pas celui du produit 1.** Utiliser la liste de ce
  document pour le produit 1 plutôt que de compter sur l'auto-description.
- **Il n'existe que deux produits.** Vérifié le 2026-09-07 : `products/2` à `products/8`
  répondent tous `404 {"error":"Unknown product"}`. La surface de l'appareil est donc bornée à
  `0` (plateforme) et `1` (fonctions du réveil) — inutile d'en chercher d'autres.
- **Les sous-ressources se découvrent, elles ne se devinent pas.** Douze existent en plus des
  ports racines, et aucune n'apparaît dans un index : `dataupload/{temp,hum,snd,lux,event}.1`
  (et leur `/data`), `wualm/{aenvs,aalms,alctr,prfwu}`, et **`files/{lightthemes,
  dusklightthemes,wakeup,winddowndusk}`** — ces dernières donnent les *noms* des thèmes
  lumineux et sonores (« Sunny day », « Forest Birds », « Soft Rain »…), ce qu'il faut pour
  présenter un choix intelligible dans l'application plutôt qu'un numéro.
- **Le port `security` livre la clé sans authentification** ; le port `fac` (reset usine)
  est également exposé. Le réveil n'a aucun contrôle d'accès sur le LAN : raison
  supplémentaire de l'isoler.
- **`tmser` vaut `http://www.noserver.com`** et `tmsrc` vaut `irq` : l'heure ne vient pas
  d'un NTP configurable mais de la liaison cloud. À surveiller après l'isolement — c'est le
  risque le plus concret de la coupure d'internet (dérive de l'horloge, changement d'heure).
- **`pysomneo` couvre 11 des 21 ports** et ignore notamment `wungt`, donc tout le suivi de
  sommeil. Il faudra des appels directs en complément — voir la colonne du §4, et le §8 pour
  ce qui mérite de remonter en amont.

### Sémantique des erreurs — mesurée le 2026-09-06

| Requête | Réponse |
| --- | --- |
| Port inexistant (`wusrd` → `zzzzz`) | `422 {"error":"No such Port"}` |
| Produit inexistant (`products/9`) | `404 {"error":"Unknown product"}` |
| Sous-ressource inexistante (`wusrd/zzz`) | `422 {"error":"No such Port"}` |
| Port connu avec barre oblique finale (`wualm/`) | **`200`** — renvoie l'arbre complet du port |
| Casse différente (`WUSRD`) | `422` — les noms sont sensibles à la casse |
| Nom de 64 caractères | **expire**, aucune réponse |

Deux points à retenir. `wualm/` renvoie `200` avec l'ensemble `snztm` / `aenvs` / `alctr` /
`aalms` / `prfnr` : une barre oblique finale n'est pas une erreur, c'est un raccourci. Et un
**nom de port très long fait expirer l'appareil** au lieu de produire un `422` — sur un
appareil sans aucun contrôle d'accès sur le LAN, c'est une raison de plus de l'isoler.

### Coût d'un `422`, et ce qu'il rend possible

Un port inconnu est refusé en **16 ms de médiane** — trente fois moins qu'une lecture réussie
sur connexion neuve. L'exhaustivité, que le relevé du 31 août jugeait hors d'atteinte, était
donc accessible. **Elle a été faite le 2026-09-07** (voir §4) : les deux balayages de 17 576
noms ont livré `dsi` et `fac`, et prouvé que la liste des ports en `wu` est complète.

> **L'estimation de durée était fausse d'un facteur quatre, et la cause vaut d'être notée.**
> 16 ms par `422` donnaient « environ 36 minutes » ; chaque balayage en a pris **152**, soit
> 520 ms par nom. L'écart n'est pas dans l'appareil mais dans le client : la sonde ouvre une
> connexion neuve à chaque nom, et l'établissement TCP + poignée de main TLS coûte ~480 ms —
> exactement l'écart mesuré au §7 entre connexion neuve et connexion réutilisée. Les 16 ms
> étaient le coût du `422` *sur une connexion déjà ouverte*.
>
> Un balayage en keep-alive tiendrait donc bien dans la demi-heure annoncée. Cela ne change
> rien à la conclusion — les deux domaines utiles sont balayés — mais c'est ce qui rendrait
> l'espace des 4 lettres (456 976 noms) tout à fait abordable : **~66 heures** en connexion
> neuve (456 976 × 519 ms), contre **~2 heures** en keep-alive (456 976 × 16 ms).
>
> **Chiffres corrigés le 2026-09-07** : « 66 jours » et « 2 jours » avaient été écrits plus tôt
> le même jour, par confusion entre heures et jours. L'erreur changeait la conclusion — elle
> faisait passer pour irréalisable un balayage qui tient dans une soirée.

### L'index du produit 1, chiffré

Trois tentatives, trois `500`, à **5,5 secondes** chacune — parfaitement reproductible.
L'index du produit 0, lui, répond en 571 ms. Ce n'est pas un aléa : c'est le seul échec
systématique de l'appareil.

### Une connexion inactive survit — hypothèse écartée

Le rapporteur de l'issue #8 décrivait un second symptôme : « if I leave the `Somneo` object to
sit around for a minute or so, the thing hangs ». Testé le 2026-09-06 sur connexion réutilisée,
trois répétitions par palier de repos : **12/12 réussites à 10 s, 30 s, 60 s et 90 s**, avec des
latences de 28 à 132 ms. Le phénomène n'est **pas reproduit** sur ce firmware. À ne pas
présenter comme réfuté chez lui pour autant — son appareil et son firmware de 2022 sont
inconnus.

## 8. Contribuer à `pysomneo` — ce qui est réellement nouveau

État du dépôt amont au 31 août 2026 : actif (dernier push le 30 août), GPL-3.0, 4 issues
ouvertes, et **toutes les PR externes depuis décembre 2022 ont été fusionnées** (6
contributeurs différents) ; les deux seules écartées datent de 2021 et d'avril 2022. Le
mainteneur accepte les apports extérieurs.

**Ce qui n'est PAS nouveau.** L'issue #16 (« Additional settings found », février 2024)
documente déjà `wufmr`, `wufmp/00`, `wurlx`, `wungt`, `wutmr` et `wutms`, obtenus par la même
méthode — lecture du code de l'application. Elle est ouverte depuis deux ans et demi sans que
personne n'en ait fait une PR. La liste des ports n'est donc pas une découverte ; **la
transformer en code l'est**.

**Ce qui est nouveau, et par ordre de valeur :**

1. **Les agrégats `dataupload/*/data`** (§5). N'apparaissent ni dans `pysomneo`, ni dans
   l'issue #16, ni nulle part ailleurs publiquement. Min, max et histogrammes de bruit et de
   lumière sur la fenêtre courante — de la donnée que la bibliothèque n'expose pas du tout.
2. **Le décodage binaire de `wusts`** (§4). `pysomneo` utilise une table de 8 valeurs magiques
   qui échoue sur toute combinaison non listée ; les tests de bits exacts sont dans l'app.
   Remplacement à correction de bug, pas seulement à ajout de fonction.
3. **Les réglages d'afficheur** (`dspon`, `brght` bornés 1–6) — réponse directe à **l'issue
   #13**, ouverte depuis mai 2023.
4. **Le mécanisme d'abonnement UDP** (§6). Absent de `pysomneo`, qui n'interroge qu'en
   boucle.
5. **Le diagnostic de l'issue #8** (ouverte depuis 2022, jamais résolue), établi par la mesure
   le 2026-09-06 : **ce qui casse l'appareil est la concurrence, à partir de deux connexions.**
   Sérialisé, il encaisse tout — vingt requêtes d'affilée sans pause, 1 h 45 de sondage
   continu, 63/63 en trois séries, tas immobile.

   **Le fil a été lu en entier, et il faut lui rendre ce qui lui revient.** Le rapporteur a
   retiré lui-même l'hypothèse `Session` dès le 26 décembre 2022, puis observé le lendemain la
   limite à une connexion TLS. Le fil s'achève en mars 2024 sur « ça marche maintenant, je ne
   sais pas par quelle magie », et le mainteneur écrit ne pas pouvoir reproduire.

   Ce que la mesure apporte n'est donc **pas la cause** — elle était devinée — mais le
   **mécanisme chiffré** et la **marche exacte**, plus l'explication de la guérison de 2024 :
   activer la réutilisation TLS supprime la seconde connexion. Le correctif n'est pas
   d'abandonner la `Session`, c'est de garantir qu'une seule requête est en vol.
6. **La sémantique des champs laissés en « ? »** dans l'issue #16 (`maxpr`, `rtype`, `intny`,
   `gdngt`, `gdday`, `prfvs`, `pwrsv`, `ctype`, `curve`), résolue par les annotations
   `@SerializedName` de l'application.

**Marche à suivre suggérée**, cohérente avec les usages du dépôt (petit mainteneur) :
commenter d'abord les issues #16, #13 et #8 avec les éléments ci-dessus, puis proposer des PR
courtes et séparées plutôt qu'un gros apport — en commençant par #13 (petite, fermée par
quelques lignes) avant `wungt` et les agrégats.

## 9. Méthode (reproductible)

1. Découverte SSDP depuis un hôte du LAN (§1).
2. Relevé direct : `GET` sur chaque port, en HTTPS sans vérification de certificat.
3. APK récupéré en XAPK (`com.philips.src.hss`, 3.22.0-rc.1), APK de base extrait.
4. Table de chaînes des trois `classes*.dex` lue directement (en-tête DEX à `0x38` :
   `string_ids_size` / `string_ids_off`, puis ULEB128 + MUTF-8) — c'est ce qui a livré les
   noms de ports `wufmp`, `wutms`, `wuwdw`, `wusds` qu'aucune devinette n'aurait trouvés.
5. Décompilation `jadx` (JRE et jadx installés en local, sans droits root), puis lecture de
   `com.philips.cdp2.brighteyes.{ports,models}` et de
   `com.philips.connectivity.condor.lan.communication`.

L'outillage a été monté dans un répertoire temporaire et n'est pas versionné : tout est
reproductible à partir de cette section.
