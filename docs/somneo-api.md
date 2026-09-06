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

> **Limite d'exhaustivité, à assumer.** Le produit 0 est exhaustif : il s'auto-décrit. Le
> produit 1 **ne l'est pas**. Cette liste couvre ce que *l'application* utilise ; un port que
> le firmware exposerait sans que SleepMapper y touche n'y figurerait pas. Une vingtaine de
> noms plausibles ont été testés en complément (`wuvol`, `wudsp`, `wuwiz`, `wuevt`…), tous en
> `422` — mais l'espace des noms de cinq lettres ne peut pas être balayé sur un appareil qui
> tombe en timeout. **Considérer cette liste comme un minorant vérifié, pas comme une preuve
> de complétude.**

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
| `wifiui` | `DeviceConnectionPort` | État WiFi, **RSSI** | ❌ |
| `fac` | — | Réinitialisation usine (`{"wifi":0,"reset":0}`) | ❌ |
| `wutim` | **aucune** | **Horloge locale de l'appareil** — découvert le 2026-09-06 | ❌ |

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
confondre avec le **champ** `wutim` du port `wusts`, qui compte des minutes restantes.

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

| Clé | Sens |
| --- | --- |
| `tg2bd` | Horodatage de mise au lit (`go to bed`) — **provient du geste**, vérifié le 2026-09-06 |
| `tendb` | Horodatage de sortie du lit (`end bed`) — **ne provient pas d'une mesure**, voir ci-dessous |
| `ntstr` `ntend` `ntlen` | Début, fin et durée de nuit (vides hors session) |
| `night` | Session de nuit active — **seul champ écrit par l'app** (`getKeyMapForNight`) |

> **`tendb` est calculé, pas mesuré — observation du 2026-09-06, à confirmer.** Relevé sur
> l'appareil : `tg2bd` = `2026-09-06T01:20:04`, `tendb` = `2026-09-06T13:20:04`. Exactement
> douze heures d'écart, à la seconde. Or le propriétaire s'est couché vers 01 h 20 en appuyant
> sur le bouton de SleepMapper, et **levé vers 09 h 40** : `tendb` ne correspond à aucun
> événement réel. La valeur ressemble à un `tg2bd + 12 h` posé par défaut.
>
> Conséquence si cela se confirme : **le réveil ne détecte pas la fin de nuit.** Un collecteur
> qui clôturerait une session sur `tendb` inventerait une heure de lever. Une seule observation
> à ce jour ; un second point est attendu de la capture de la nuit du 6 au 7 septembre 2026.

**`wualm/prfwu` — profil d'alarme.** `prfnr` (n° 1-16), `pname`, `prfen` (activé),
`prfvs` (visible), `almhr`/`almmn`, `daynm` (masque de jours), `ayear`/`amnth`/`alday`
(date pour une alarme unique), `curve` (intensité du lever de soleil), `durat` (durée),
`ctype` (thème lumineux), `snddv`/`sndch`/`sndlv`/`sndss` (source, canal, volume, départ
en douceur), `pwrsz`/`pszhr`/`pszmn` (PowerWake), `snztm` (snooze), `lgtds`.

Masque `daynm` : bit 1 = lundi … bit 7 = dimanche. `62` = jours ouvrés, `192` = week-end,
`254` = tous les jours, `0` = demain uniquement.

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

Les autres champs du port se lisent tels quels : `snztm`, `nrcur`, `pwrsz`, `fmrna`,
`wutim`/`dutim`/`sntim` (minutes restantes ; **`65535` = inactif**), `rpair`, `hmlay`.

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

- `svper` : secondes écoulées dans la fenêtre courante (plafond 900 s).
- `av*` / `lo*` / `hi*` : **moyenne, minimum, maximum** sur la fenêtre.
- `ab*` / `rl*` (bruit et lumière seulement) : **histogrammes**, `[n, [borne_basse, borne_haute,
  compte] × n]` — `ab*` sur les valeurs absolues, `rl*` sur les variations. Le relevé ci-dessus
  se lit : 11 484 échantillons entre 0 et 30 dB, 35 970 entre 30 et 40, 42 256 au-dessus de 40.

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

> **Non testé.** Souscrire est un `POST` qui crée un état persistant sur l'appareil ; l'essai
> n'a pas été fait pour ne pas laisser de trace sur un réveil en service. À tenter avec un
> `ttl` court et un écouteur UDP sur la Radxa, avant de bâtir la collecte dessus.

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
- **Conséquence attendue d'une réécriture asynchrone — inférence, pas encore vérifiée contre
  l'appareil** : deux coroutines qui interrogent simultanément amènent le client à ouvrir une
  seconde connexion, ce qui est exactement la condition d'échec mesurée. La sérialisation
  devrait donc être garantie par construction — un verrou, ou une limite de connexions à 1 —
  et non par la seule discipline d'appel.
- **L'espacement de 200 ms reste une prudence raisonnable**, mais ce n'est pas lui qui évite
  les échecs : c'est le fait de n'avoir qu'une requête en vol.
- **L'index `/di/v1/products/1/` expire toujours en `500`** — lui seul, de façon reproductible.
  C'est le seul échec constaté à ce jour.
- **`/di/v1/products/0/` fonctionne, pas celui du produit 1.** Utiliser la liste de ce
  document pour le produit 1 plutôt que de compter sur l'auto-description.
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
sur connexion neuve. Balayer l'espace entier des noms `wu` + trois lettres (17 576
combinaisons) prend donc **environ 36 minutes**, sérialisé. L'exhaustivité, que le relevé du
31 août jugeait hors d'atteinte, est en fait accessible : elle reste à faire.

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
