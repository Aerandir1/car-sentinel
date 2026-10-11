"""Mode démo : un trajet simulé (démarrage, accélérations, croisière, freinage, arrêt), en boucle.

Tout se passe comme avec la voiture : une session, des mesures horodatées, des trajets, l'envoi au serveur.
Mais dans une base à part (data/clio-demo.db), envoyée au serveur comme une autre origine (« … · démo ») :
les vrais trajets ne sont jamais mêlés à la démo. Supprimer data/clio-demo.db efface la démo locale.
"""

import random
import time

from etat import METRICS

HZ = 10
BARO = 101.3
AMBIENT = 16.0
IDLE_RPM = 820
VIN = "VF1DEMO0000000000"   # faux numéro de série, reconnaissable
# km/h → tr/min sur chaque rapport (boîte 5 vitesses du K9K : 100 km/h ≈ 2300 tr/min en 5e)
GEARS = [120, 65, 42, 30, 23]
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
    """Simule tant que still_on() est vrai."""
    rnd = random.Random()
    noise = lambda x, r: x + rnd.uniform(-r, r)
    speed, coolant, oil, step, t_step, gear = 0.0, 45.0, 40.0, 0, 0.0, 0
    state.supported, state.unsupported = [k for k, *_ in METRICS], []
    state.start_session("démo · valeurs simulées", VIN, state.supported + ["boost"])
    state.status = "connecté (démo, valeurs simulées)"
    dt = 1 / HZ
    while still_on():
        t0 = time.time()
        dur, pedal_target, brake = SCRIPT[step]
        t_step += dt
        if t_step >= dur:
            step, t_step = (step + 1) % len(SCRIPT), 0.0
        engine_off = pedal_target is None
        pedal = max(0.0, noise(pedal_target, 3) if pedal_target else 0.0)

        # Vitesse : poussée de la pédale, frottements, freins
        speed += (pedal / 100 * 9 - 0.5 - 0.0006 * speed ** 2 - brake) * dt
        speed = max(0.0, speed)
        if engine_off:
            rpm = 0.0
        elif speed < 4:
            rpm = noise(IDLE_RPM + pedal * 15, 15)
        else:   # passage des rapports avec hystérésis : on monte vers 2400 tr/min (3200 à fond), on rétrograde sous 1300
            up = 3200 if pedal > 70 else 2400
            if gear < len(GEARS) - 1 and speed * GEARS[gear] > up:
                gear += 1
            elif gear > 0 and speed * GEARS[gear] < 1300:
                gear -= 1
            rpm = noise(max(IDLE_RPM, speed * GEARS[gear]), 20)
        if speed < 4:
            gear = 0

        load = 0.0 if engine_off else noise(18 + pedal * 0.8, 1.5) if pedal else noise(8 if speed > 4 else 18, 1)
        boost = max(-0.02, min(1.35, (rpm - 1400) / 1300 * load / 100 * 1.6))
        coolant += (0.25 if coolant < 89 else 0.0) * dt * (1 + load / 50)
        coolant = min(coolant, noise(90.5, 0.3))
        oil += (coolant + 6 - oil) * 0.004
        values = {
            "rpm": rpm, "speed": speed,
            "coolant": coolant, "oil": oil,
            "intake": AMBIENT + 8 + boost * 18,
            "map": BARO + boost * 100, "baro": BARO,
            "load": load, "pedal": pedal,
            "throttle": 100.0 if pedal else 88.0,
            "maf": rpm / 1000 * (7 + boost * 14),
            "rail": 0.0 if engine_off else 280 + load * 11 + rpm * 0.12,
            "fuel": 0.0 if engine_off else 0.55 + load / 100 * rpm / 1000 * 3.2,
            "egr": 0.0 if engine_off else 35.0 if load < 45 and rpm < 2600 else 4.0,
            "egr_err": noise(0, 2.5),
            "ambient": AMBIENT,
            "voltage": noise(14.15, 0.04),
        }
        for key, v in values.items():
            state.update(key, round(v, 2))
        state.derive()
        time.sleep(max(0.0, dt - (time.time() - t0)))
        state.hz = 1.0 / max(time.time() - t0, 1e-3)
