"""Boucles d'acquisition, lancées dans un thread : lecture de l'ECU par l'ELM327, ou simulation (--demo)."""

import random
import time

from etat import BARO_DEFAULT_KPA, METRICS


def obd_loop(state, args, logger_factory):
    import obd  # importé ici pour que --demo marche sans python-obd

    state.source = args.port or "auto"
    while True:
        fast = state.get_settings()["fast"]
        state.status = "connexion (mode rapide)…" if fast else "connexion…"
        conn = obd.OBD(portstr=args.port, baudrate=args.baud, fast=fast, timeout=1)
        if conn.status() != obd.OBDStatus.CAR_CONNECTED:
            state.status = f"pas de réponse ECU ({conn.status()}), nouvel essai dans 5 s"
            conn.close()
            time.sleep(5)
            continue

        cmds = []
        state.supported, state.unsupported = [], []
        for key, _lbl, _u, name, conv, *_ in METRICS:
            cmd = getattr(obd.commands, name, None)
            if cmd is not None and conn.supports(cmd):
                cmds.append((key, cmd, conv))
                state.supported.append(key)
            else:
                state.unsupported.append(key)
        state.set_source(f"{conn.port_name()} · {conn.protocol_name()}")
        state.status = "connecté (rapide)" if fast else "connecté"
        logger = logger_factory(state.supported + (["boost"] if "map" in state.supported else []))

        try:
            while conn.is_connected():
                if state.get_settings()["fast"] != fast:
                    break  # mode rapide basculé : on se reconnecte avec le nouveau réglage
                t0 = time.time()
                delay = state.get_settings()["delay"]
                for key, cmd, conv in cmds:
                    r = conn.query(cmd)
                    if r.is_null():
                        state.update(key, None)
                        continue
                    v = r.value.magnitude if hasattr(r.value, "magnitude") else r.value
                    v = float(conv(v) if conv else v)
                    state.update(key, round(v, 2))
                    if delay:
                        time.sleep(delay)
                state.derive()
                # Bride la boucle à max_hz (l'ELM327 est souvent plus lent de toute façon)
                time.sleep(max(0.0, 1.0 / state.get_settings()["max_hz"] - (time.time() - t0)))
                state.hz = 1.0 / max(time.time() - t0, 1e-3)
                if logger:
                    logger.maybe_write(state)
        finally:
            if logger:
                logger.close()
            conn.close()
        if state.get_settings()["fast"] != fast:
            continue
        state.status = "connexion perdue, reconnexion…"
        time.sleep(2)


def demo_loop(state, args, logger_factory):
    """Simule un trajet de Clio 4 dCi 90 : accélérations, croisière, ralenti."""
    keys = [m[0] for m in METRICS if m[0] != "egr_err"]
    state.supported = keys
    state.unsupported = [m[0] for m in METRICS if m[0] not in keys]
    state.set_source("simulation dCi 90")
    state.status = "démo"
    logger = logger_factory(keys + ["boost"])

    ratios = [0, 118, 63, 40, 28, 21]  # tr/min par km/h, BVM5 du dCi 90 (approx.)
    speed, gear, coolant, oil = 0.0, 1, 18.0, 15.0
    target, phase_end = 50.0, 0.0
    while True:
        dt = 1.0 / state.get_settings()["max_hz"]
        now = time.time()
        if now > phase_end:
            target = random.choice([0, 30, 50, 50, 70, 90, 110, 130])
            phase_end = now + random.uniform(8, 20)

        accel = max(-3.0, min(2.2, (target - speed) * 0.25))
        speed = max(0.0, speed + accel * dt * 3.6)
        while gear < 5 and speed * ratios[gear] > 2300:
            gear += 1
        while gear > 1 and speed * ratios[gear] < 1300:
            gear -= 1
        rpm = max(820.0, speed * ratios[gear]) + random.uniform(-15, 15)
        pedal = max(0.0, min(100.0, 15 + accel * 35 + speed * 0.15)) if accel > -0.5 else 0.0
        load = max(8.0, min(100.0, pedal * 0.9 + random.uniform(-2, 2)))
        boost_kpa = max(0.0, (load - 25) * 1.6) * min(1.0, rpm / 1750)
        coolant = min(89.0, coolant + dt * 0.12 * (1 + load / 50))
        oil = min(98.0, oil + dt * 0.07 * (1 + load / 50))

        for key, value in {
            "rpm": rpm, "speed": speed, "coolant": coolant, "oil": oil,
            "intake": 22 + boost_kpa * 0.12, "map": BARO_DEFAULT_KPA + boost_kpa + random.uniform(-1, 1),
            "baro": BARO_DEFAULT_KPA, "load": load, "pedal": pedal, "throttle": 88 + random.uniform(-2, 2),
            "fuel": 0.7 + load * rpm / 25000, "egr": max(0.0, 55 - load) if coolant > 60 else 0.0,
            "ambient": 14.0,
            "maf": 4 + rpm / 1000 * (3 + load * 0.25), "rail": 280 + load * 12 + rpm * 0.08,
            "voltage": 14.2 + random.uniform(-0.1, 0.1),
        }.items():
            state.update(key, round(value, 2))
        state.derive()
        state.hz = 1 / dt
        if logger:
            logger.maybe_write(state)
        time.sleep(dt)
