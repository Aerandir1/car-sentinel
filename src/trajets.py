"""Découpage des mesures brutes en trajets, fiche de chaque trajet et événements.

Tout est calculé à partir de la table samples, qui n'est jamais modifiée : changer un seuil puis
lancer `python src/trajets.py --voiture clio --rebuild` recalcule tout (les identifiants et les notes sont gardés).

  marche moteur  régime > ENGINE_ON_RPM, sans trou de plus de RUN_GAP_S entre deux mesures ;
                 un régime bas pendant moins de ENGINE_OFF_S (lecture ratée) ne coupe pas
  trajet         marches moteur séparées de moins de TRIP_GAP_S (arrêt court : même trajet)
                 'trajet' si la voiture a roulé, 'arret' si le moteur a tourné sans rouler
  carburant      débit (L/h) intégré sur le trajet ; coût = carburant × prix au litre réglé dans le dashboard
                 pendant le trajet (gardé ensuite : changer le prix ne touche pas les trajets finis)
  fermé          moteur arrêté depuis plus de TRIP_GAP_S : le trajet ne bougera plus
  éco-conduite   voir driving() et eco_score() : accélérations et freinages brusques, rapports montés tard, pied
                 lourd, ralenti prolongé, frein moteur ; un score sur 100 pour les trajets d'au moins ECO_MIN_KM.
                 Et le régulateur (temps, km et carburant), hors score
  tronçons       boutons de type de route du mode Conduite : chaque appui (mesure « troncon », valeur : code de
                 ROADS) ouvre un tronçon, numéroté à partir de 1 dans le trajet ; voir sections()
"""

import argparse
import time
from pathlib import Path

import donnees
import voitures

ENGINE_ON_RPM = 400       # au-dessus : moteur allumé
ENGINE_OFF_S = 3          # régime bas au moins aussi longtemps : vrai arrêt moteur
RUN_GAP_S = 10            # plus longtemps sans mesure du régime : le moteur a pu s'arrêter
TRIP_GAP_S = 5 * 60       # arrêt moteur plus long : fin du trajet
MOVING_KMH = 5            # vitesse max au-dessus : la voiture a roulé
COLD_START_C = 50         # eau plus froide au démarrage : démarrage à froid
# Éco-conduite. Propres à chaque voiture (clio.py, fiesta.py) : TOP_GEAR_RATIO (régime / vitesse au-dessus duquel
# on n'est pas sur le dernier rapport), SHIFT_RPM (au-delà, sur un rapport intermédiaire : il fallait monter),
# PEDAL_HEAVY (% de pédale au-delà : pied lourd)
HARD_ACCEL = 2.0          # m/s², sur ACCEL_WINDOW_S : accélération brusque (départ arrêté à 0 → 20 km/h en 2,5 s)
HARD_BRAKE = -2.5         # m/s² : freinage brusque
ACCEL_WINDOW_S = 2        # vitesse lue toutes les ≈ 1,3 s : accélération sur au moins 2 s
IDLE_LONG_S = 60          # moteur à l'arrêt plus longtemps : ralenti prolongé (couper le moteur)
COAST_KMH = 20            # au-dessus, débit de carburant sous COAST_LH : coupure d'injection (frein moteur)
COAST_LH = 0.2
# Régulateur : pédale au repos (la vitesse est tenue par le calculateur) au-dessus de CRUISE_KMH pendant au moins
# CRUISE_MIN_S, moteur qui injecte au moins la moitié du temps (sinon : lever de pied en frein moteur)
CRUISE_KMH = 50
CRUISE_PEDAL = 1          # % de la course : au repos
CRUISE_MIN_S = 10
ECO_MIN_KM = 1            # trajet plus court : pas de score
# Tronçons : type de route choisi à chaque appui, (code enregistré, clé, libellé). Code aussi enregistré chaque
# seconde sous « road », et le numéro du tronçon sous « section », tant qu'il dure (etat.py) : étiquettes des mesures
ROADS = ((1, "ville", "Ville"), (2, "route", "Route"), (3, "rapide", "Voie rapide"), (4, "bouchons", "Bouchons"))
ROAD_LABELS = {code: label for code, _k, label in ROADS}
SECTION_EARLY_S = 60      # appui jusqu'à une minute avant le démarrage du moteur : compte dans le trajet


def warns(car):
    """Seuils d'alerte de la voiture : clé → (libellé, seuil)."""
    return {k: (lbl, warn) for k, lbl, _u, _c, _cv, _r, warn, _cold in car.METRICS if warn is not None}


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


def accel_events(speed):
    """[(t_ms, km/h)] → (accélérations brusques, freinages brusques). Un événement dure tant que l'accélération
    reste au-delà de la moitié du seuil."""
    acc = brk = 0
    in_acc = in_brk = False
    j = 0
    for i, (t, v) in enumerate(speed):
        while j + 1 < i and t - speed[j + 1][0] >= ACCEL_WINDOW_S * 1000:
            j += 1
        dt = (t - speed[j][0]) / 1000
        if j >= i or not ACCEL_WINDOW_S <= dt <= 2 * ACCEL_WINDOW_S:
            in_acc = in_brk = False   # trou dans les mesures
            continue
        a = (v - speed[j][1]) / 3.6 / dt
        if a > HARD_ACCEL and not in_acc:
            acc += 1
        if a < HARD_BRAKE and not in_brk:
            brk += 1
        in_acc = a > HARD_ACCEL / 2 if in_acc else a > HARD_ACCEL
        in_brk = a < HARD_BRAKE / 2 if in_brk else a < HARD_BRAKE
    return acc, brk


def driving(db, start, end, car):
    """Temps passé (s) dans chaque façon de conduire, d'après les dernières valeurs lues de vitesse, régime, pédale
    et débit de carburant (chaque intervalle entre deux mesures compte pour l'état d'avant). Plus le régulateur :
    temps, km et carburant dessous, et km et carburant au-dessus de CRUISE_KMH sans lui (pour comparer)."""
    keys = ("speed", "rpm", "pedal", "fuel")
    rows = db.execute(f"SELECT t_ms, key, value FROM samples WHERE key IN ({', '.join('?' * len(keys))})"
                      " AND t_ms BETWEEN ? AND ? ORDER BY t_ms", (*keys, start, end)).fetchall()
    cur, prev = {}, None
    out = dict.fromkeys(("drive_s", "idle_s", "idle_long_s", "late_shift_s", "heavy_s", "coast_s"), 0.0)
    idle_fuel, idle_run, has = 0.0, 0.0, set()
    # Régulateur : [s, s en injection, km, L] du passage pédale au repos en cours, et les totaux
    seg = [0.0, 0.0, 0.0, 0.0]
    cruise, fast = [0.0, 0.0, 0.0], [0.0, 0.0]   # (s, km, L) sous régulateur ; (km, L) au-dessus de 50 sans

    def end_seg():
        if seg[0] >= CRUISE_MIN_S and seg[1] >= seg[0] / 2:
            cruise[0] += seg[0]; cruise[1] += seg[2]; cruise[2] += seg[3]
        else:
            fast[0] += seg[2]; fast[1] += seg[3]
        seg[:] = [0.0, 0.0, 0.0, 0.0]

    for t, key, v in rows:
        if prev is not None and 0 < t - prev <= RUN_GAP_S * 1000:
            dt = (t - prev) / 1000
            speed, rpm, pedal, fuel = (cur.get(k) for k in keys)
            on_road = speed is not None and speed >= CRUISE_KMH and fuel is not None
            if on_road and pedal is not None and pedal <= CRUISE_PEDAL:
                seg[0] += dt; seg[2] += speed * dt / 3600; seg[3] += fuel * dt / 3600
                if fuel >= COAST_LH:
                    seg[1] += dt
            else:
                end_seg()
                if on_road:
                    fast[0] += speed * dt / 3600; fast[1] += fuel * dt / 3600
            if speed is not None and rpm is not None and rpm > ENGINE_ON_RPM:
                if speed < 1:
                    out["idle_s"] += dt
                    idle_run += dt
                    if fuel is not None:
                        idle_fuel += fuel * dt / 3600
                else:
                    out["drive_s"] += dt
                    if speed >= 10 and rpm > car.SHIFT_RPM and rpm / speed > car.TOP_GEAR_RATIO:
                        out["late_shift_s"] += dt
                    if fuel is not None and speed >= COAST_KMH and fuel < COAST_LH:
                        out["coast_s"] += dt
                if pedal is not None and pedal > car.PEDAL_HEAVY:
                    out["heavy_s"] += dt
            if not (speed is not None and speed < 1 and rpm is not None and rpm > ENGINE_ON_RPM):
                if idle_run > IDLE_LONG_S:
                    out["idle_long_s"] += idle_run
                idle_run = 0.0
        else:
            end_seg()   # trou dans les mesures
        if v is not None:
            cur[key] = v
            has.add(key)
        prev = t
    end_seg()
    if idle_run > IDLE_LONG_S:
        out["idle_long_s"] += idle_run
    out = {k: round(v, 1) for k, v in out.items()}
    if "pedal" not in has:
        out["heavy_s"] = None
    if "fuel" not in has:
        out["coast_s"] = None
    out["idle_fuel_l"] = round(idle_fuel, 3) if "fuel" in has else None
    ok = "pedal" in has and "fuel" in has
    out["cruise_s"] = round(cruise[0], 1) if ok else None
    out["cruise_km"] = round(cruise[1], 2) if ok else None
    out["cruise_fuel_l"] = round(cruise[2], 3) if ok else None
    out["fast_km"] = round(fast[0], 2) if ok else None
    out["fast_fuel_l"] = round(fast[1], 3) if ok else None
    out["hard_accels"], out["hard_brakes"] = accel_events(
        [(t, v) for t, k, v in rows if k == "speed" and v is not None])
    return out


def eco_penalties(s):
    """Points perdus sur chaque défaut d'éco-conduite (plafonnés), None pour un trajet trop court."""
    km, drive = s["distance_km"] or 0, s["drive_s"] or 0
    if km < ECO_MIN_KM or drive < 60:
        return None
    per_10km = 10 / max(km, 5)   # trajet court : un seul départ vif ne fait pas tout le score
    return {
        "accel": min(25, 5 * s["hard_accels"] * per_10km),   # 1 accélération brusque tous les 10 km : -5
        "brake": min(25, 5 * s["hard_brakes"] * per_10km),
        "shift": min(20, 150 * s["late_shift_s"] / drive),     # 10 % du temps à monter les rapports trop tard : -15
        "pedal": min(15, 150 * (s["heavy_s"] or 0) / drive),
        "idle": min(10, 50 * s["idle_long_s"] / (drive + s["idle_s"])),   # ralenti prolongé, en part du temps moteur
    }


def eco_score(s):
    """Score d'éco-conduite sur 100 : 100 moins les points perdus. None pour un trajet trop court."""
    p = eco_penalties(s)
    return None if p is None else round(max(0.0, 100 - sum(p.values())))


def stats(db, start, end, runs, car):
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
    out = {
        "vin": row[0] if row else None,
        "distance_km": round(integrate(series("speed")), 2),
        "fuel_l": round(integrate(fuel), 3) if any(v is not None for _t, v in fuel) else None,
        "coolant_start": coolant,
        "cold_start": None if coolant is None else int(coolant < COLD_START_C),
        "max_rpm": mx("rpm"), "max_speed": mx("speed"), "max_coolant": mx("coolant"), "max_boost": mx("boost"),
        "engine_stops": len(runs) - 1,
        "missing_ratio": round(sum(n - nv for _mx, n, nv in by_key.values()) / total, 4) if total else None,
        **driving(db, start, end, car),
    }
    out["eco_score"] = eco_score(out)
    return out


def presses(db, lo, hi):
    """Appuis sur un bouton de tronçon entre lo et hi : [(t_ms, code de ROADS)]."""
    return db.execute("SELECT t_ms, value FROM samples WHERE key = 'troncon' AND value IS NOT NULL"
                      " AND t_ms BETWEEN ? AND ? ORDER BY t_ms", (lo, hi)).fetchall()


def sections(db, start, end, detail=True):
    """Tronçons d'un trajet (end : dernière mesure pour un trajet en cours), dans l'ordre. detail : durée, distance,
    carburant, vitesses ; sinon seulement numéro, type et début (repères des courbes)."""
    p = presses(db, start - SECTION_EARLY_S * 1000, end)
    out = []
    for i, (t0, code) in enumerate(p):
        t1 = p[i + 1][0] if i + 1 < len(p) else end
        sec = {"n": i + 1, "road": int(code), "label": ROAD_LABELS.get(int(code), "?"), "start_ms": t0, "end_ms": t1}
        if detail:
            series = lambda key: db.execute("SELECT t_ms, value FROM samples WHERE key = ? AND t_ms BETWEEN ? AND ?"
                                            " ORDER BY t_ms", (key, t0, t1)).fetchall()
            speed, fuel = series("speed"), series("fuel")
            km = integrate(speed)
            hours = max(t1 - t0, 1) / 3_600_000
            sec.update(duration_s=round((t1 - t0) / 1000), distance_km=round(km, 2),
                       fuel_l=round(integrate(fuel), 3) if any(v is not None for _t, v in fuel) else None,
                       avg_speed=round(km / hours, 1),
                       max_speed=max((v for _t, v in speed if v is not None), default=None))
        out.append(sec)
    return out


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


def events(db, from_ms, runs, now, car):
    """Démarrages, arrêts moteur et alertes après from_ms (recalculés à chaque passage)."""
    out = []
    for i, (a, b) in enumerate(runs):
        out.append((a, "demarrage", None, None, None))
        if i < len(runs) - 1 or now - b > RUN_GAP_S * 1000:
            out.append((b, "arret_moteur", None, None, None))
    for key, (label, warn) in warns(car).items():
        above = None
        for t, v in db.execute("SELECT t_ms, value FROM samples WHERE key = ? AND t_ms > ? AND value IS NOT NULL"
                               " ORDER BY t_ms", (key, from_ms)):
            if v > warn and above is False:   # passage au-dessus du seuil
                out.append((t, "alerte", key, v, f"{label} > {warn}"))
            above = v > warn
    db.execute("DELETE FROM events WHERE t_ms > ?", (from_ms,))
    db.executemany("INSERT INTO events (t_ms, kind, key, value, detail) VALUES (?, ?, ?, ?, ?)", out)


def update(path, rebuild=False, price=None, car=None):
    """Met à jour trajets et événements avec les nouvelles mesures. price : prix du carburant (€/L) du moment ;
    car : voiture de la base (seuils d'éco-conduite et d'alerte). Renvoie la fiche du trajet en cours (ou None)."""
    car = car or voitures.get(None)
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
                s = stats(db, start, end, trip_runs, car)
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
            events(db, from_ms, runs, now, car)
    finally:
        db.close()
    return current


def main():
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--voiture", choices=sorted(voitures.CARS), default=voitures.DEFAULT,
                   help=f"voiture dont on recalcule les trajets (défaut {voitures.DEFAULT})")
    p.add_argument("--db", help="base SQLite (défaut data/<voiture>.db)")
    p.add_argument("--rebuild", action="store_true", help="recalcule tous les trajets depuis le début")
    args = p.parse_args()
    car = voitures.get(args.voiture)
    args.db = args.db or here / "data" / f"{car.ID}.db"
    donnees.init(args.db, car)
    update(args.db, rebuild=args.rebuild, car=car)
    for t in reversed(donnees.trips(args.db)):
        cost = f"  {t['fuel_l'] * t['fuel_price']:6.2f} €" if t["fuel_l"] is not None and t["fuel_price"] else ""
        fuel = f"  {t['fuel_l']:6.2f} L" if t["fuel_l"] is not None else ""
        print(f"{t['name']:40} {(t['end_ms'] - t['start_ms']) / 60000:6.1f} min  {t['distance_km'] or 0:7.2f} km"
              + fuel + cost + ("" if t["closed"] else "  (en cours)"))


if __name__ == "__main__":
    main()
