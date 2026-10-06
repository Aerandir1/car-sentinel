"""Enregistrement des mesures dans une base SQLite, chaque valeur avec son horodatage.

Une ligne par valeur lue : la clé de la métrique, l'instant de la réponse du calculateur (ms depuis
le 1er janvier 1970, UTC) et la valeur (NULL si le calculateur n'a pas répondu). Les mesures sont
regroupées par trajet (un lancement du script = un trajet).

    sqlite3 data/clio.db "SELECT datetime(t_ms / 1000, 'unixepoch', 'localtime'), key, value
                          FROM samples WHERE trip_id = 1 ORDER BY t_ms LIMIT 20"
"""

import queue
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS trips (
    id        INTEGER PRIMARY KEY,
    start_ms  INTEGER NOT NULL,          -- première mesure
    end_ms    INTEGER,                   -- dernière mesure enregistrée
    source    TEXT                       -- adaptateur et protocole, ou « simulation »
);
CREATE TABLE IF NOT EXISTS samples (
    trip_id   INTEGER NOT NULL REFERENCES trips(id),
    t_ms      INTEGER NOT NULL,          -- horodatage de la réponse, ms UTC
    key       TEXT    NOT NULL,          -- clé de METRICS (rpm, coolant…) ou valeur calculée (boost)
    value     REAL                       -- NULL : pas de réponse du calculateur
);
CREATE INDEX IF NOT EXISTS samples_trip_key_t ON samples(trip_id, key, t_ms);
"""

FLUSH_S = 1.0          # écriture groupée : une transaction par seconde
MAX_POINTS = 2000      # au-delà, l'historique renvoyé est moyenné par tranches de temps


def now_ms():
    return int(time.time() * 1000)


def connect(path):
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA journal_mode=WAL")    # lectures (page web) pendant l'écriture
    db.execute("PRAGMA synchronous=NORMAL")
    return db


class Recorder:
    """File d'attente des mesures, vidée dans la base par un thread toutes les FLUSH_S secondes."""

    def __init__(self, path):
        self.path = path
        self.queue = queue.Queue()
        self.trip_id = None
        self.source = None
        db = connect(path)
        db.executescript(SCHEMA)
        db.close()
        threading.Thread(target=self._run, daemon=True).start()

    def add(self, key, t_ms, value):
        self.queue.put((t_ms, key, value))

    def set_source(self, source):
        self.source = source

    def _run(self):
        db = connect(self.path)
        while True:
            time.sleep(FLUSH_S)
            rows = []
            while True:
                try:
                    rows.append(self.queue.get_nowait())
                except queue.Empty:
                    break
            if not rows:
                continue
            with db:
                if self.trip_id is None:
                    cur = db.execute("INSERT INTO trips (start_ms, end_ms, source) VALUES (?, ?, ?)",
                                     (rows[0][0], rows[-1][0], self.source))
                    self.trip_id = cur.lastrowid
                db.executemany("INSERT INTO samples (trip_id, t_ms, key, value) VALUES (?, ?, ?, ?)",
                               [(self.trip_id, t, k, v) for t, k, v in rows])
                db.execute("UPDATE trips SET end_ms = ?, source = coalesce(?, source) WHERE id = ?",
                           (max(r[0] for r in rows), self.source, self.trip_id))


def trips(path):
    db = connect(path)
    try:
        rows = db.execute("""SELECT t.id, t.start_ms, t.end_ms, t.source,
                                    (SELECT count(*) FROM samples s WHERE s.trip_id = t.id)
                             FROM trips t ORDER BY t.id DESC""").fetchall()
    finally:
        db.close()
    return [{"id": i, "start_ms": a, "end_ms": b, "source": src, "samples": n} for i, a, b, src, n in rows]


def history(path, trip_id, key, from_ms=None, to_ms=None):
    """Points [t_ms, valeur] d'une métrique sur un trajet, moyennés si plus de MAX_POINTS."""
    db = connect(path)
    try:
        if trip_id is None:
            row = db.execute("SELECT max(id) FROM trips").fetchone()
            trip_id = row[0]
        if trip_id is None:
            return {"trip_id": None, "key": key, "points": []}
        lo, hi = db.execute("SELECT min(t_ms), max(t_ms) FROM samples WHERE trip_id = ? AND key = ?"
                            " AND t_ms >= ? AND t_ms <= ?",
                            (trip_id, key, from_ms or 0, to_ms or 2**62)).fetchone()
        if lo is None:
            return {"trip_id": trip_id, "key": key, "points": []}
        args = (trip_id, key, from_ms or 0, to_ms or 2**62)
        n = db.execute("SELECT count(*) FROM samples WHERE trip_id = ? AND key = ? AND t_ms >= ? AND t_ms <= ?",
                       args).fetchone()[0]
        if n <= MAX_POINTS:
            pts = db.execute("SELECT t_ms, value FROM samples WHERE trip_id = ? AND key = ?"
                             " AND t_ms >= ? AND t_ms <= ? ORDER BY t_ms", args).fetchall()
        else:
            step = max(1, (hi - lo) // MAX_POINTS + 1)
            pts = db.execute("SELECT min(t_ms), avg(value) FROM samples WHERE trip_id = ? AND key = ?"
                             " AND t_ms >= ? AND t_ms <= ? GROUP BY (t_ms - ?) / ? ORDER BY 1",
                             args + (lo, step)).fetchall()
    finally:
        db.close()
    return {"trip_id": trip_id, "key": key, "points": [[t, None if v is None else round(v, 3)] for t, v in pts]}
