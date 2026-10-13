"""Mode démo : un trajet simulé (démarrage, accélérations, croisière, freinage, arrêt), en boucle.

Tout se passe comme avec la voiture : une session, des mesures horodatées, des trajets, l'envoi au serveur.
Mais dans une base à part (data/fiesta-demo.db), envoyée au serveur comme une autre origine (« … · démo ») :
les vrais trajets ne sont jamais mêlés à la démo. Supprimer data/fiesta-demo.db efface la démo locale.
"""

import random
import time

from etat import METRICS

HZ = 10
BARO = 101.3
AMBIENT = 16.0
IDLE_RPM = 900
ETHANOL = 72.0   # E85 en hiver (≈ 70 % d'éthanol à la pompe), lu par le PCM sur le capteur de carburant
VIN = "WF0DEMO0000000000"   # faux numéro de série, reconnaissable (WF0 : Ford Allemagne)
# Valeurs brutes des mesures Ford à étalonner (onglet Étalonnage) : brut = (valeur - décalage) / gain,
# avec une échelle volontairement différente de celle par défaut (ford.FALLBACK) pour avoir à la corriger
RAW = {"tip": (0.0081, 0.0), "octane": (0.0049, 0.0), "wgdc": (0.0025, 0.0)}
# km/h → tr/min sur chaque rapport (boîte manuelle 5 vitesses de la 1.0 EcoBoost 95 : 100 km/h ≈ 2700 tr/min en 5e)
GEARS = [140, 78, 50, 36, 27]
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
    state.sources = {k: {"kind": "demo", "cmd": None} for k in state.supported}
    state.raw_keys = list(RAW)
    state.start_session("démo · valeurs simulées", VIN,
                        state.supported + ["boost"] + [f"brut.{k}" for k in RAW])
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

        load = 0.0 if engine_off else noise(15 + pedal * 0.85, 1.5) if pedal else noise(6 if speed > 4 else 16, 1)
        # Essence à papillon : dépression au ralenti et pied levé, le turbo souffle sous charge à partir de ~1500 tr/min
        spool = max(0.0, min(1.0, (rpm - 1500) / 1000))
        boost = -0.72 + load / 100 * 0.75 + spool * max(0.0, load - 45) / 55 * 1.05
        boost = max(-0.75, min(1.1, noise(boost, 0.01)))
        tip = BARO + max(boost, 0.0) * 100 + 3 + spool * load * 0.2   # avant le papillon : jamais en dépression
        coolant += (0.3 if coolant < 92 else 0.0) * dt * (1 + load / 50)
        coolant = min(coolant, noise(92.5, 0.3))
        cht = coolant + 6 + load * 0.15
        oil += (coolant + 4 - oil) * 0.004
        fuel = 0.0 if engine_off else (0.45 + load / 100 * rpm / 1000 * 2.4) * (1 + ETHANOL / 100 * 0.35)
        values = {
            "rpm": rpm, "speed": speed, "gear": 0 if engine_off or speed < 4 else gear + 1,
            "coolant": coolant, "cht": cht, "oil": oil,
            "intake": AMBIENT + 6, "cac": AMBIENT + 10 + max(boost, 0) * 25,
            "cat": 0.0 if engine_off else 420 + load * 4.5 + rpm * 0.04,
            "ambient": AMBIENT,
            "map": BARO + boost * 100, "tip": tip, "baro": BARO,
            "wgdc": 0.0 if engine_off else max(0.0, min(100.0, 85 - spool * load * 0.9)),
            "load": load, "pedal": pedal,
            "throttle": 0.0 if engine_off else 4.0 + pedal * 0.8 if pedal else 3.5,
            "maf": rpm / 1000 * (2.2 + max(boost + 0.75, 0) * 6),
            "ethanol": ETHANOL,
            "fuel": fuel,
            "fuel_lvl": 63.0,
            "rail": 0.0 if engine_off else 40 + load * 1.4,
            "rail_des": 0.0 if engine_off else 40 + load * 1.4,
            "lambda": 1.0 if pedal < 85 else 0.88,
            "stft": noise(0, 3), "ltft": noise(2.3, 0.2),
            "timing": 0.0 if engine_off else 12 - load * 0.08 + (rpm - 900) / 400,
            "ign_corr": 0.0 if load < 70 else noise(-1.2, 0.6),
            "octane": 100.0 * min(1.0, ETHANOL / 70),
            "voltage": noise(14.3, 0.04),
            "oil_life": 78.0,
        }
        for key, v in values.items():
            state.update(key, round(v, 2))
        for key, (gain, offset) in RAW.items():
            state.update(f"brut.{key}", round((values[key] - offset) / gain))
        state.derive()
        time.sleep(max(0.0, dt - (time.time() - t0)))
        state.hz = 1.0 / max(time.time() - t0, 1e-3)
