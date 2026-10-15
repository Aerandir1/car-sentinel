"""Découpage des mesures brutes en trajets, fiche de chaque trajet et événements.

Tout est calculé à partir de la table samples, qui n'est jamais modifiée : changer un seuil puis
lancer `python src/trajets.py --rebuild` recalcule tout (les identifiants et les notes sont gardés).

  marche moteur  régime > ENGINE_ON_RPM, sans trou de plus de RUN_GAP_S entre deux mesures ;
                 un régime bas pendant moins de ENGINE_OFF_S (lecture ratée) ne coupe pas
  trajet         marches moteur séparées de moins de TRIP_GAP_S (arrêt court : même trajet)
                 'trajet' si la voiture a roulé, 'arret' si le moteur a tourné sans rouler
  carburant      débit (L/h) intégré sur le trajet ; coût = carburant × prix au litre réglé dans le dashboard
                 pendant le trajet (gardé ensuite : changer le prix ne touche pas les trajets finis)
  fermé          moteur arrêté depuis plus de TRIP_GAP_S : le trajet ne bougera plus
"""

import argparse
import time
from pathlib import Path

import donnees
from etat import METRICS

ENGINE_ON_RPM = 400       # au-dessus : moteur allumé
ENGINE_OFF_S = 3          # régime bas au moins aussi longtemps : vrai arrêt moteur
RUN_GAP_S = 10            # plus longtemps sans mesure du régime : le moteur a pu s'arrêter
TRIP_GAP_S = 5 * 60       # arrêt moteur plus long : fin du trajet
MOVING_KMH = 5            # vitesse max au-dessus : la voiture a roulé
COLD_START_C = 50         # eau plus froide au démarrage : démarrage à froid
WARN = {k: (lbl, warn) for k, lbl, _u, _c, _cv, _r, warn, _cold in METRICS if warn is not None}


def engine_runs(rows):
    """[(t_ms, régime)] triés → [(début, fin)] des périodes moteur allumé."""
    runs, cur = [], None
    for t, v in rows:
        if v is None:
            continue   # pas de réponse : le trou est jugé par RUN_GAP_S
        on = v > ENGINE_ON_RPM
        if cur and (not on or t - cur[1] > RUN_GAP_S * 1000):
            runs.append(tuple(cur))
            cur = None
        if on:
            cur = cur or [t, t]
            cur[1] = t
    if cur:
        runs.append(tuple(cur))
    merged = []
    for run in runs:
        if merged and run[0] - merged[-1][1] < ENGINE_OFF_S * 1000:
            merged[-1] = (merged[-1][0], run[1])
        else:
            merged.append(run)
    return merged


def group_runs(runs):
    """Marches moteur → trajets [(début, fin, [marches])]."""
    trips = []
    for run in runs:
        if trips and run[0] - trips[-1][1] < TRIP_GAP_S * 1000:
            trips[-1] = (trips[-1][0], run[1], trips[-1][2] + [run])
        else:
            trips.append((run[0], run[1], [run]))
    return trips


def integrate(rows):
    """Intègre [(t_ms, valeur par heure)] : km pour une vitesse, litres pour un débit de carburant.
    Les trous de plus de RUN_GAP_S ne comptent pas (même calcul en direct dans etat.py)."""
    d, prev = 0.0, None
    for t, v in rows:
        if v is None:
            continue
        if prev and t - prev[0] <= RUN_GAP_S * 1000:
            d += (prev[1] + v) / 2 * (t - prev[0]) / 3_600_000
        prev = (t, v)
    return d


def stats(db, start, end, runs):
    by_key = {k: (mx, n, nv) for k, mx, n, nv in db.execute(
        "SELECT key, max(value), count(*), count(value) FROM samples WHERE t_ms BETWEEN ? AND ? GROUP BY key",
        (start, end))}
    series = lambda key: db.execute("SELECT t_ms, value FROM samples WHERE key = ? AND t_ms BETWEEN ? AND ?"
                                    " ORDER BY t_ms", (key, start, end)).fetchall()
    fuel = series("fuel")
    row = db.execute("SELECT value FROM samples WHERE key = 'coolant' AND value IS NOT NULL"
                     " AND t_ms BETWEEN ? AND ? ORDER BY t_ms LIMIT 1", (start - 60_000, end)).fetchone()
    coolant = row[0] if row else None
    row = db.execute("SELECT vin FROM sessions WHERE vin IS NOT NULL AND start_ms <= ? AND end_ms >= ? LIMIT 1",
                     (end, start)).fetchone()
    total = sum(n for _mx, n, _nv in by_key.values())
    mx = lambda k: by_key[k][0] if k in by_key else None
    return {
        "vin": row[0] if row else None,
        "distance_km": round(integrate(series("speed")), 2),
        "fuel_l": round(integrate(fuel), 3) if any(v is not None for _t, v in fuel) else None,
        "coolant_start": coolant,
        "cold_start": None if coolant is None else int(coolant < COLD_START_C),
        "max_rpm": mx("rpm"), "max_speed": mx("speed"), "max_coolant": mx("coolant"), "max_boost": mx("boost"),
        "engine_stops": len(runs) - 1,
        "missing_ratio": round(sum(n - nv for _mx, n, nv in by_key.values()) / total, 4) if total else None,
    }


def trip_name(db, kind, start):
    lt = time.localtime(start / 1000)
    day0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)) * 1000)
    day = time.strftime("%Y-%m-%d", lt)
    hhmm = time.strftime("%H:%M", lt)
    if kind == "arret":
        return f"{day} · moteur à l'arrêt · {hhmm}"
    n = db.execute("SELECT count(*) FROM trips WHERE kind = 'trajet' AND start_ms >= ? AND start_ms < ?",
                   (day0, start)).fetchone()[0]
    return f"{day} · trajet {n + 1} · {hhmm}"


def events(db, from_ms, runs, now):
    """Démarrages, arrêts moteur et alertes après from_ms (recalculés à chaque passage)."""
    out = []
    for i, (a, b) in enumerate(runs):
        out.append((a, "demarrage", None, None, None))
        if i < len(runs) - 1 or now - b > RUN_GAP_S * 1000:
            out.append((b, "arret_moteur", None, None, None))
    for key, (label, warn) in WARN.items():
        above = None
        for t, v in db.execute("SELECT t_ms, value FROM samples WHERE key = ? AND t_ms > ? AND value IS NOT NULL"
                               " ORDER BY t_ms", (key, from_ms)):
            if v > warn and above is False:   # passage au-dessus du seuil
                out.append((t, "alerte", key, v, f"{label} > {warn}"))
            above = v > warn
    db.execute("DELETE FROM events WHERE t_ms > ?", (from_ms,))
    db.executemany("INSERT INTO events (t_ms, kind, key, value, detail) VALUES (?, ?, ?, ?, ?)", out)


def update(path, rebuild=False, price=None):
    """Met à jour trajets et événements avec les nouvelles mesures. price : prix du carburant (€/L) du moment.
    Renvoie la fiche du trajet en cours (ou None)."""
    now = donnees.now_ms()
    db = donnees.connect(path)
    try:
        with db:
            if rebuild:
                from_ms = 0
                existing = db.execute("SELECT id, start_ms, end_ms, note, fuel_price FROM trips").fetchall()
            else:
                from_ms = db.execute("SELECT coalesce(max(end_ms), 0) FROM trips WHERE closed = 1").fetchone()[0]
                existing = db.execute("SELECT id, start_ms, end_ms, note, fuel_price FROM trips WHERE closed = 0").fetchall()
            rpm = db.execute("SELECT t_ms, value FROM samples WHERE key = 'rpm' AND t_ms > ? ORDER BY t_ms",
                             (from_ms,)).fetchall()
            runs = engine_runs(rpm)
            used, current = set(), None
            db.executemany("DELETE FROM trips WHERE id = ?", [(e[0],) for e in existing])
            for start, end, trip_runs in group_runs(runs):
                # Même trajet qu'avant (il chevauche) : on garde son identifiant et sa note
                match = max((e for e in existing if e[0] not in used and e[1] <= end and e[2] >= start),
                            key=lambda e: min(e[2], end) - max(e[1], start), default=None)
                tid, note, old_price = (match[0], match[3], match[4]) if match else (donnees.new_id(), None, None)
                used.add(tid)
                s = stats(db, start, end, trip_runs)
                kind = "trajet" if (s["max_speed"] or 0) > MOVING_KMH else "arret"
                closed = now - end > TRIP_GAP_S * 1000
                # Prix du moment pour un trajet en cours ; un trajet fini garde le sien (recalcul, --rebuild)
                fuel_price = old_price if closed and old_price is not None else price
                row = {"id": tid, "kind": kind, "name": trip_name(db, kind, start), "start_ms": start,
                       "end_ms": end, "closed": int(closed), "note": note, "fuel_price": fuel_price, **s}
                if not closed:
                    current = row
                db.execute(f"INSERT INTO trips ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})",
                           tuple(row.values()))
            if price is not None:   # trajets d'avant le premier prix réglé : ils prennent celui-ci
                db.execute("UPDATE trips SET fuel_price = ? WHERE fuel_price IS NULL", (price,))
                if current and current["fuel_price"] is None:
                    current["fuel_price"] = price
            events(db, from_ms, runs, now)
    finally:
        db.close()
    return current


def main():
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=here / "data" / "fiesta.db", help="base SQLite (défaut data/fiesta.db)")
    p.add_argument("--rebuild", action="store_true", help="recalcule tous les trajets depuis le début")
    args = p.parse_args()
    donnees.init(args.db)
    update(args.db, rebuild=args.rebuild)
    for t in reversed(donnees.trips(args.db)):
        cost = f"  {t['fuel_l'] * t['fuel_price']:6.2f} €" if t["fuel_l"] is not None and t["fuel_price"] else ""
        fuel = f"  {t['fuel_l']:6.2f} L" if t["fuel_l"] is not None else ""
        print(f"{t['name']:40} {(t['end_ms'] - t['start_ms']) / 60000:6.1f} min  {t['distance_km'] or 0:7.2f} km"
              + fuel + cost + ("" if t["closed"] else "  (en cours)"))


if __name__ == "__main__":
    main()
