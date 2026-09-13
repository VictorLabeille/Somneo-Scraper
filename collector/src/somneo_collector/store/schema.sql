-- Schéma du collecteur — SQLite, WAL. Plan technique §3.
-- Un seul écrivain (la boucle de collecte et les commandes, sérialisées) ; lecteurs concurrents
-- (les routes de l'API). Toute ligne exposée à l'app porte un `seq`, compteur global croissant,
-- alloué par `meta.next_seq`, repris à chaque insertion ET à chaque modification (rattrapage §7).
--
-- La base vit sur la carte, jamais dans le dépôt : elle peut contenir numéro de série et
-- conditions de chambre. Les fixtures de test, elles, n'ont que des formes (plan §9).

PRAGMA user_version = 1;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
INSERT OR IGNORE INTO meta (key, value) VALUES ('next_seq', 1);

-- Un appareil par numéro de série. Un nouveau numéro = une rupture datée ; la série continue.
CREATE TABLE IF NOT EXISTS device (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    serial     TEXT UNIQUE,
    model      TEXT,
    firmware   TEXT,
    first_seen REAL NOT NULL,
    last_seen  REAL NOT NULL,
    seq        INTEGER NOT NULL
);

-- Les points mesurés de `wusrd` : 8 grandeurs typées, NULL pour une absente. Chaque lecture.
CREATE TABLE IF NOT EXISTS reading (
    seq   INTEGER PRIMARY KEY,
    ts    REAL NOT NULL,
    mslux REAL, mstmp REAL, msrhu REAL, mssnd REAL,
    avlux REAL, avtmp REAL, avrhu REAL, avsnd REAL
);
CREATE INDEX IF NOT EXISTS idx_reading_ts ON reading (ts);

-- `dataupload/{temp,hum,snd,lux}.1/data` : moyenne, min, max, histogrammes (JSON). Au changement.
-- `ts` = heure de PREMIÈRE lecture de la fenêtre servie (jamais l'heure de la fenêtre : inconnue).
CREATE TABLE IF NOT EXISTS window_aggregate (
    seq    INTEGER PRIMARY KEY,
    ts     REAL NOT NULL,
    kind   TEXT NOT NULL,           -- temp | hum | snd | lux
    avg    REAL, lo REAL, hi REAL,
    hist   TEXT                     -- JSON brut (ab*/rl*), NULL pour temp/hum
);
CREATE INDEX IF NOT EXISTS idx_wagg_kind_ts ON window_aggregate (kind, ts);

-- Corps bruts des ports d'état, à chaque changement. C'est le miroir historisé.
CREATE TABLE IF NOT EXISTS port_change (
    seq  INTEGER PRIMARY KEY,
    ts   REAL NOT NULL,
    port TEXT NOT NULL,
    body TEXT NOT NULL              -- JSON brut, tel que l'appareil l'a rendu
);
CREATE INDEX IF NOT EXISTS idx_portchange_port_ts ON port_change (port, ts);

-- Périodes d'indisponibilité, nommées. Quatre causes (§3) : collecteur arrêté, réveil
-- injoignable, appareil saturé, carte hors réseau.
CREATE TABLE IF NOT EXISTS outage (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    seq      INTEGER NOT NULL,
    start    REAL NOT NULL,
    end      REAL,                  -- NULL tant que l'indisponibilité dure
    cause    TEXT NOT NULL,
    failures INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_outage_start ON outage (start);

-- Une seule ligne, réécrite chaque minute : borne un arrêt du collecteur.
CREATE TABLE IF NOT EXISTS heartbeat (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    ts REAL NOT NULL
);

-- Chaque contrôle d'horloge (§6), pas seulement ceux qui précèdent une correction : c'est la
-- courbe de dérive. `corrected` reste 0 — l'heure ne s'écrit pas (§2.E) ; colonne gardée stable.
CREATE TABLE IF NOT EXISTS clock_check (
    seq            INTEGER PRIMARY KEY,
    ts             REAL NOT NULL,
    card_time      REAL,
    device_time    REAL,            -- port `time`, à la seconde
    wutim_time     REAL,            -- horloge décomposée
    offset_time_s  REAL,            -- device_time - card_time
    offset_wutim_s REAL,            -- wutim_time - card_time
    corrected      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_clock_ts ON clock_check (ts);

-- ---- Nuits : écrites à l'incrément 2. Tables posées dès maintenant pour figer le schéma. ----

-- `id` stable identifie la nuit (API, corrections, machine à états) ; `seq` est repris à chaque
-- modification pour le rattrapage — il ne peut donc pas servir de clé d'identité.
CREATE TABLE IF NOT EXISTS night (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    seq        INTEGER NOT NULL,
    day        TEXT NOT NULL,       -- jour local (Europe/Paris) de l'heure de coucher
    bedtime    REAL,                -- heure de coucher, référentiel COLLECTEUR (NTP) — contrat §F
    risetime   REAL,
    state      TEXT NOT NULL,       -- pending_device | open | closed | abnormal
    bedtime_origin  TEXT,           -- confirmed | observed | pending
    risetime_origin TEXT,           -- confirmed | estimated
    raw_tg2bd  TEXT,                -- valeur brute du réveil (wutim), traçabilité, jamais servie
    raw_tendb  TEXT
);
CREATE INDEX IF NOT EXISTS idx_night_day ON night (day);
CREATE INDEX IF NOT EXISTS idx_night_seq ON night (seq);

CREATE TABLE IF NOT EXISTS night_correction (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    seq       INTEGER NOT NULL,
    night_id  INTEGER NOT NULL REFERENCES night (id),
    ts        REAL NOT NULL,
    field     TEXT NOT NULL,        -- bedtime | risetime
    value     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_gesture (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,          -- heure de l'appui (collecteur), fait foi
    kind    TEXT NOT NULL,          -- bedtime | risetime
    applied INTEGER NOT NULL DEFAULT 0
);
