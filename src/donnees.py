"""Enregistrement des mesures dans une base SQLite, chaque valeur avec son horodatage.

Brut, jamais modifié (sauf une échelle corrigée au passage à un nouveau format, voir init) :
  sessions  une connexion au calculateur (contact mis), avec l'adaptateur, le VIN, la version du script
  samples   une ligne par valeur lue : métrique, instant de la réponse (ms UTC), valeur (NULL : pas de réponse)
Calculé à partir du brut, recalculable (voir trajets.py) :
  trips     les trajets (et les périodes moteur allumé sans rouler), avec leur fiche et une note libre
  events    démarrages, arrêts moteur, alertes

    sqlite3 data/fiesta.db "SELECT datetime(t_ms / 1000, 'unixepoch', 'localtime'), key, value
                          FROM samples ORDER BY t_ms DESC LIMIT 20"
"""

import json
import queue
import sqlite3
import threading
import time
import traceback
import uuid

SCHEMA_VERSION = 5
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id        TEXT PRIMARY KEY,          -- identifiant unique (UUID)
    start_ms  INTEGER NOT NULL,          -- première mesure
    end_ms    INTEGER,                   -- dernière mesure enregistrée
    source    TEXT,                      -- adaptateur et protocole
    vin       TEXT,                      -- numéro de série de la voiture, lu sur le calculateur
    version   TEXT,                      -- version du script (commit git)
    metrics   TEXT                       -- liste JSON des métriques lues
);
CREATE TABLE IF NOT EXISTS samples (
    session_id TEXT    NOT NULL REFERENCES sessions(id),
    t_ms       INTEGER NOT NULL,         -- horodatage de la réponse, ms UTC
    key        TEXT    NOT NULL,         -- clé de METRICS (rpm, coolant…) ou valeur calculée (boost)
    value      REAL                      -- NULL : pas de réponse du calculateur
);
CREATE INDEX IF NOT EXISTS samples_key_t ON samples(key, t_ms);
CREATE INDEX IF NOT EXISTS samples_t ON samples(t_ms);
CREATE TABLE IF NOT EXISTS trips (
    id            TEXT PRIMARY KEY,      -- identifiant unique (UUID), gardé quand le trajet est recalculé
    kind          TEXT NOT NULL,         -- 'trajet' (la voiture a roulé) ou 'arret' (moteur allumé sans rouler)
    name          TEXT NOT NULL,         -- « 2026-10-06 · trajet 1 · 08:01 »
    start_ms      INTEGER NOT NULL,      -- démarrage du moteur
    end_ms        INTEGER NOT NULL,      -- dernier instant moteur allumé
    closed        INTEGER NOT NULL,      -- 1 : moteur arrêté depuis plus de 5 min, le trajet ne bougera plus
    vin           TEXT,
    distance_km   REAL,
    coolant_start REAL,                  -- température d'eau au démarrage
    cold_start    INTEGER,               -- 1 : démarrage à froid
    max_rpm       REAL,
    max_speed     REAL,
    max_coolant   REAL,
    max_boost     REAL,
    engine_stops  INTEGER,               -- arrêts moteur de moins de 5 min dans le trajet
    missing_ratio REAL,                  -- part des requêtes sans réponse du calculateur
    note          TEXT,                  -- note libre, saisie dans le dashboard
    fuel_l        REAL,                  -- carburant consommé (débit intégré), NULL sans mesure de conso
    fuel_price    REAL,                  -- prix du carburant (€/L) réglé pendant le trajet : coût = fuel_l × fuel_price
    -- Éco-conduite (voir trajets.py), temps en s
    drive_s       REAL,                  -- moteur allumé, voiture qui roule
    idle_s        REAL,                  -- moteur allumé à l'arrêt
    idle_long_s   REAL,                  -- dont arrêts de plus d'une minute (ralenti prolongé)
    idle_fuel_l   REAL,                  -- carburant brûlé à l'arrêt
    late_shift_s  REAL,                  -- au-delà de 2500 tr/min sur un rapport intermédiaire
    heavy_s       REAL,                  -- pied lourd (pédale à plus de 70 % de sa course), NULL sans mesure
    coast_s       REAL,                  -- frein moteur, injection coupée, NULL sans mesure de conso
    hard_accels   INTEGER,               -- accélérations brusques
    hard_brakes   INTEGER,               -- freinages brusques
    eco_score     INTEGER,               -- score sur 100, NULL pour un trajet de moins d'1 km
    -- Régulateur (pédale au repos au-dessus de 50 km/h, moteur qui tire), NULL sans mesure de pédale ou de conso
    cruise_s      REAL,
    cruise_km     REAL,
    cruise_fuel_l REAL,
    fast_km       REAL,                  -- au-dessus de 50 km/h sans régulateur : de quoi comparer la conso
    fast_fuel_l   REAL
);
CREATE INDEX IF NOT EXISTS trips_start ON trips(start_ms);
CREATE TABLE IF NOT EXISTS events (
    t_ms    INTEGER NOT NULL,
    kind    TEXT NOT NULL,               -- 'demarrage', 'arret_moteur', 'alerte'
    key     TEXT,                        -- métrique concernée (alerte)
    value   REAL,
    detail  TEXT
);
CREATE INDEX IF NOT EXISTS events_t ON events(t_ms);
"""

# Colonnes ajoutées aux trajets depuis la version 1 (ajoutées à une base plus ancienne)
TRIP_ADDED = (("fuel_l", "REAL"), ("fuel_price", "REAL"), ("drive_s", "REAL"), ("idle_s", "REAL"),
              ("idle_long_s", "REAL"), ("idle_fuel_l", "REAL"), ("late_shift_s", "REAL"), ("heavy_s", "REAL"),
              ("coast_s", "REAL"), ("hard_accels", "INTEGER"), ("hard_brakes", "INTEGER"), ("eco_score", "INTEGER"),
              ("cruise_s", "REAL"), ("cruise_km", "REAL"), ("cruise_fuel_l", "REAL"), ("fast_km", "REAL"),
              ("fast_fuel_l", "REAL"))

FLUSH_S = 1.0          # écriture groupée : une transaction par seconde
MAX_POINTS = 2000      # au-delà, l'historique renvoyé est moyenné par tranches de temps


def now_ms():
    return int(time.time() * 1000)


def new_id():
    return str(uuid.uuid4())


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")    # lectures (page web) pendant l'écriture
    db.execute("PRAGMA synchronous=NORMAL")
    return db


def init(path, car=None):
    """Crée la base, ou la met au format actuel (reprend les mesures de l'ancien format : un trajet par lancement).
    car : voiture de la base (module fiesta, clio), pour les conversions qui ne concernent qu'elle."""
    db = connect(path)
    try:
        with db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version < SCHEMA_VERSION:
                old = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
                if "trips" in old and "samples" in old and "sessions" not in old:
                    db.execute("ALTER TABLE trips RENAME TO old_trips")
                    db.execute("ALTER TABLE samples RENAME TO old_samples")
                    db.execute("DROP INDEX IF EXISTS samples_trip_key_t")
                db.executescript(SCHEMA)
                if "old_trips" in {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}:
                    for tid, start, end, source in db.execute("SELECT id, start_ms, end_ms, source FROM old_trips").fetchall():
                        sid = new_id()
                        db.execute("INSERT INTO sessions (id, start_ms, end_ms, source) VALUES (?, ?, ?, ?)",
                                   (sid, start, end, source))
                        db.execute("INSERT INTO samples SELECT ?, t_ms, key, value FROM old_samples WHERE trip_id = ?",
                                   (sid, tid))
                    db.execute("DROP TABLE old_samples")
                    db.execute("DROP TABLE old_trips")
                # Version 2 : carburant et coût des trajets. Versions 3 et 5 : éco-conduite, régulateur. Les trajets
                # sont rouverts pour que trajets.py les recalcule tous au prochain passage (identifiants, notes et
                # prix gardés)
                trip_cols = {r[1] for r in db.execute("PRAGMA table_info(trips)")}
                for col, kind in TRIP_ADDED:
                    if col not in trip_cols:
                        db.execute(f"ALTER TABLE trips ADD COLUMN {col} {kind}")
                # Version 4 : pédale de la Fiesta ramenée à sa course (0 au repos, 100 au plancher, voir
                # fiesta.pedal_travel) au lieu du % de tension lu. Les mesures déjà enregistrées sont converties, celles
                # de la Fiesta seulement (VIN WF0, dans n'importe quelle base : avant la fusion, chaque dashboard a pu
                # enregistrer l'autre voiture), pas la démo, déjà en course ; sans VIN, seulement dans la base de la
                # Fiesta (la pédale de la Clio est déjà en %). La vérification de synchro.py renvoie les groupes
                # corrigés au serveur.
                if 1 <= version < 4:
                    import fiesta
                    db.execute("UPDATE samples SET value = round(max(0.0, min(100.0, (value - ?) * 100 / ?)), 1)"
                               " WHERE key = 'pedal' AND value IS NOT NULL AND session_id IN"
                               " (SELECT id FROM sessions WHERE (vin LIKE 'WF0%' AND vin NOT LIKE 'WF0DEMO%')"
                               + (" OR vin IS NULL" if car is fiesta else "") + ")",
                               (fiesta.PEDAL_REST, fiesta.PEDAL_FULL - fiesta.PEDAL_REST))
                if version >= 1:
                    db.execute("UPDATE trips SET closed = 0")
                db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    finally:
        db.close()


class Recorder:
    """File d'attente des mesures, vidée dans la base par un thread toutes les FLUSH_S secondes."""

    def __init__(self, path, version=None, car=None):
        self.path = path
        self.version = version
        self.queue = queue.Queue()
        self.session_id = None
        init(path, car)
        threading.Thread(target=self._run, daemon=True).start()

    def start_session(self, source, vin, metrics):
        """Nouvelle connexion au calculateur : les mesures suivantes lui sont rattachées."""
        self.session_id = new_id()
        self.queue.put(("session", self.session_id, source, vin, self.version, json.dumps(metrics)))

    def add(self, key, t_ms, value):
        if self.session_id:
            self.queue.put(("sample", self.session_id, t_ms, key, value))

    def relabel(self, key, lo, hi, value=None):
        """Remplace par value les mesures key entre lo et hi (ms), ou les efface (value None), après l'écriture de
        celles déjà en file (appui annulé)."""
        self.queue.put(("relabel", key, lo, hi, value))

    def _run(self):
        db = connect(self.path)
        items = []
        while True:
            time.sleep(FLUSH_S)
            while True:
                try:
                    items.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if not items:
                continue
            try:
                self._write(db, items)
                items = []
            except sqlite3.Error:   # base verrouillée, disque plein… : on garde les mesures et on réessaie
                traceback.print_exc()
                time.sleep(5)

    @staticmethod
    def _write(db, items):
        """Une transaction : tout est écrit, ou rien (et tout est réessayé)."""
        new_sessions = {i[1]: i[2:] for i in items if i[0] == "session"}
        samples = [i[1:] for i in items if i[0] == "sample"]
        ends = {}
        for sid, t, _k, _v in samples:
            ends[sid] = max(ends.get(sid, t), t)
        with db:
            for sid, (source, vin, version, metrics) in new_sessions.items():
                t0 = min((s[1] for s in samples if s[0] == sid), default=now_ms())
                db.execute("INSERT INTO sessions (id, start_ms, end_ms, source, vin, version, metrics)"
                           " VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, t0, t0, source, vin, version, metrics))
            db.executemany("INSERT INTO samples (session_id, t_ms, key, value) VALUES (?, ?, ?, ?)", samples)
            for sid, end in ends.items():
                db.execute("UPDATE sessions SET end_ms = max(end_ms, ?) WHERE id = ?", (end, sid))
            for _op, key, lo, hi, value in (i for i in items if i[0] == "relabel"):
                if value is None:
                    db.execute("DELETE FROM samples WHERE key = ? AND t_ms BETWEEN ? AND ?", (key, lo, hi))
                else:
                    db.execute("UPDATE samples SET value = ? WHERE key = ? AND t_ms BETWEEN ? AND ?",
                               (value, key, lo, hi))


TRIP_FIELDS = ("id", "kind", "name", "start_ms", "end_ms", "closed", "vin", "distance_km", "coolant_start",
               "cold_start", "max_rpm", "max_speed", "max_coolant", "max_boost", "engine_stops",
               "missing_ratio", "note") + tuple(c for c, _ in TRIP_ADDED)


def trips(path, limit=200):
    """Trajets du plus récent au plus ancien, avec leurs alertes."""
    db = connect(path)
    try:
        rows = db.execute(f"SELECT {', '.join(TRIP_FIELDS)} FROM trips ORDER BY start_ms DESC LIMIT ?",
                          (limit,)).fetchall()
        out = []
        for r in rows:
            t = dict(zip(TRIP_FIELDS, r))
            t["closed"] = bool(t["closed"])
            t["cold_start"] = None if t["cold_start"] is None else bool(t["cold_start"])
            t["alerts"] = [{"t_ms": a, "key": k, "value": v, "detail": d} for a, k, v, d in db.execute(
                "SELECT t_ms, key, value, detail FROM events WHERE kind = 'alerte' AND t_ms BETWEEN ? AND ?"
                " ORDER BY t_ms", (t["start_ms"], t["end_ms"]))]
            out.append(t)
    finally:
        db.close()
    return out


def set_note(path, trip_id, note):
    db = connect(path)
    try:
        with db:
            n = db.execute("UPDATE trips SET note = ? WHERE id = ?", ((note or "").strip() or None, trip_id)).rowcount
    finally:
        db.close()
    return n > 0


def trip_keys(path, trip_id):
    """Mesures enregistrées sur un trajet : {clé: nombre de valeurs (réponses du calculateur)}."""
    db = connect(path)
    try:
        row = db.execute("SELECT start_ms, end_ms, closed FROM trips WHERE id = ?", (trip_id,)).fetchone()
        if row is None:
            raise ValueError("trajet inconnu")
        hi = row[1] if row[2] else 2**62   # trajet en cours : jusqu'à la dernière mesure
        rows = db.execute("SELECT key, count(value) FROM samples WHERE t_ms >= ? AND t_ms <= ? GROUP BY key",
                          (row[0], hi)).fetchall()
    finally:
        db.close()
    return {k: n for k, n in rows if n}


def history(path, trip_id, key, from_ms=None, to_ms=None):
    """Points [t_ms, valeur] d'une métrique, sur un trajet et/ou une période, moyennés si plus de MAX_POINTS.

    Sans trajet ni période : le dernier trajet.
    """
    db = connect(path)
    try:
        if trip_id is None and from_ms is None and to_ms is None:
            row = db.execute("SELECT id FROM trips ORDER BY start_ms DESC LIMIT 1").fetchone()
            trip_id = row[0] if row else None
            if trip_id is None:
                return {"trip_id": None, "key": key, "step_ms": None, "points": []}
        lo_t, hi_t = 0, 2**62
        if trip_id is not None:
            row = db.execute("SELECT start_ms, end_ms, closed FROM trips WHERE id = ?", (trip_id,)).fetchone()
            if row is None:
                raise ValueError("trajet inconnu")
            # Trajet en cours : jusqu'à la dernière mesure (sa fin en base n'est recalculée que toutes les 10 s)
            lo_t, hi_t = row[0], row[1] if row[2] else 2**62
        args = (key, max(lo_t, from_ms or 0), min(hi_t, to_ms or 2**62))
        lo, hi, n = db.execute("SELECT min(t_ms), max(t_ms), count(*) FROM samples"
                               " WHERE key = ? AND t_ms >= ? AND t_ms <= ?", args).fetchone()
        step = None
        if not n:
            pts = []
        elif n <= MAX_POINTS:
            pts = db.execute("SELECT t_ms, value FROM samples WHERE key = ? AND t_ms >= ? AND t_ms <= ?"
                             " ORDER BY t_ms", args).fetchall()
        else:
            step = max(1, (hi - lo) // MAX_POINTS + 1)
            pts = db.execute("SELECT min(t_ms), avg(value) FROM samples WHERE key = ? AND t_ms >= ? AND t_ms <= ?"
                             " GROUP BY (t_ms - ?) / ? ORDER BY 1", args + (lo, step)).fetchall()
    finally:
        db.close()
    return {"trip_id": trip_id, "key": key, "step_ms": step,
            "points": [[t, None if v is None else round(v, 3)] for t, v in pts]}
