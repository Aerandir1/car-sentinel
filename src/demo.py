"""Mode démo : un trajet simulé (démarrage, accélérations, croisière, freinage, arrêt), en boucle.

La voiture simulée est la voiture en cours (dernière vue, ou choisie dans Réglages) : la conduite est la même,
les mesures sont les siennes (demo_values de clio.py, fiesta.py).

Tout se passe comme avec la voiture : une session, des mesures horodatées, des trajets. Mais dans une base à part
(data/<voiture>-demo.db), jamais envoyée au serveur : les vrais trajets ne sont jamais mêlés à la démo. Supprimer
data/<voiture>-demo.db efface la démo locale.
"""

import math
import random
import time

from etat import GPS_KEYS

HZ = 10
GPS_START = (48.1113, -1.6800)   # départ de la position simulée (Rennes)
BARO = 101.3
AMBIENT = 16.0
# Le trajet : (durée en s, pédale en %, freinage en km/h par s)
SCRIPT = [
    (8, 0, 0),      # ralenti
    (14, 55, 0),    # démarrage, montée des rapports
    (20, 25, 0),    # ville
    (6, 0, 6),      # feu rouge
    (6, 0, 0),
    (10, 85, 0),    # insertion sur voie rapide : turbo
    (25, 32, 0),    # croisière
    (8, 0, 3),      # sortie
    (12, 20, 0),
    (8, 0, 7),      # arrêt
    (6, 0, 0),
    (8, None, 0),   # moteur coupé (stop & start) : arrêt moteur dans le trajet
]


def run(state, still_on):
    """Simule la voiture en cours tant que still_on() est vrai."""
    car = state.car
    rnd = random.Random()
    noise = lambda x, r: x + rnd.uniform(-r, r)
    step, t_step = 0, 0.0
    sim = {"speed": 0.0, "gear": 0, "coolant": 45.0, "oil": 40.0, "baro": BARO, "ambient": AMBIENT, "dt": 1 / HZ}
    state.supported, state.unsupported = [k for k, *_ in car.METRICS], []
    state.sources = {k: {"kind": "demo", "cmd": None} for k in state.supported}
    state.raw_keys = list(car.DEMO_RAW)
    state.start_session("démo · valeurs simulées", car.DEMO_VIN,
                        state.supported + ["boost"] + [f"brut.{k}" for k in car.DEMO_RAW] + ["sport", "road", "section"]
                        + list(GPS_KEYS))
    # GPS simulé : la voiture avance avec la vitesse simulée et tourne à chaque étape du trajet
    pos = {"lat": GPS_START[0], "lon": GPS_START[1], "head": 75.0, "t": 0.0}
    state.status = "connecté (démo, valeurs simulées)"
    dt = sim["dt"]
    while still_on():
        t0 = time.time()
        dur, pedal_target, brake = SCRIPT[step]
        t_step += dt
        if t_step >= dur:
            step, t_step = (step + 1) % len(SCRIPT), 0.0
            pos["head"] = (pos["head"] + rnd.choice((-90, -40, 0, 30, 90))) % 360
        engine_off = pedal_target is None
        pedal = max(0.0, noise(pedal_target, 3) if pedal_target else 0.0)

        # Vitesse : poussée de la pédale, frottements, freins
        speed = max(0.0, sim["speed"] + (pedal / 100 * 9 - 0.5 - 0.0006 * sim["speed"] ** 2 - brake) * dt)
        gear = sim["gear"]
        if engine_off:
            rpm = 0.0
        elif speed < 4:
            rpm = noise(car.IDLE_RPM + pedal * 15, 15)
        else:   # passage des rapports avec hystérésis : on monte vers 2400 tr/min (3200 à fond), on rétrograde sous 1300
            up = 3200 if pedal > 70 else 2400
            if gear < len(car.GEARS) - 1 and speed * car.GEARS[gear] > up:
                gear += 1
            elif gear > 0 and speed * car.GEARS[gear] < 1300:
                gear -= 1
            rpm = noise(max(car.IDLE_RPM, speed * car.GEARS[gear]), 20)
        if speed < 4:
            gear = 0
        sim.update(speed=speed, gear=gear, rpm=rpm, pedal=pedal, engine_off=engine_off)

        values = car.demo_values(sim, noise)
        for key, v in values.items():
            state.update(key, round(v, 2))
        for key, (gain, offset) in car.DEMO_RAW.items():
            state.update(f"brut.{key}", round((values[key] - offset) / gain))
        state.derive()
        # Position : déplacement à la vitesse simulée, une position GPS par seconde
        d = speed / 3.6 * dt
        pos["lat"] += d * math.cos(math.radians(pos["head"])) / 111_320
        pos["lon"] += d * math.sin(math.radians(pos["head"])) / (111_320 * math.cos(math.radians(pos["lat"])))
        pos["t"] += dt
        if pos["t"] >= 1:
            pos["t"] = 0.0
            state.gps_fix(pos["lat"], pos["lon"], speed, noise(52, 1), pos["head"] if speed > 3 else None, noise(5, 1))
        time.sleep(max(0.0, dt - (time.time() - t0)))
        state.hz = 1.0 / max(time.time() - t0, 1e-3)
