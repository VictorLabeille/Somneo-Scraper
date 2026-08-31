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
| `tg2bd` | Horodatage de mise au lit (`go to bed`) |
| `tendb` | Horodatage de sortie du lit (`end bed`) |
| `ntstr` `ntend` `ntlen` | Début, fin et durée de nuit (vides hors session) |
| `night` | Session de nuit active — **seul champ écrit par l'app** (`getKeyMapForNight`) |

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

`brght` est borné à **1–6** (`isWithinLimit()` dans l'app). C'est la réponse à l'issue #13
de `pysomneo`, ouverte depuis 2023.

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

- **L'appareil sature.** `heap_free` ≈ 25 ko : l'index `/di/v1/products/1/` expire en `500`,
  et des rafales de requêtes provoquent des timeouts. Sérialiser les appels, espacer d'environ
  200 ms, et prévoir des relances — c'est un ThreadX, pas un serveur.
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
5. **La cause des timeouts de l'issue #8** (ouverte depuis 2022, 9 commentaires, jamais
   résolue) : le port `mem` montre ~25 ko de tas libre. Ce n'est pas la faute de `Session`
   comme le suppose le rapporteur, c'est un appareil qui n'a pas la mémoire de servir des
   requêtes concurrentes. Diagnostic mesurable, pas conjectural.
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
