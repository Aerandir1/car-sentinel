"""Boucle d'acquisition, lancée dans un thread : lecture de l'ECU par l'ELM327."""

import time

from etat import METRICS

MUTE_S = 10   # plus aucune réponse du calculateur depuis ce temps (contact coupé) : fin de la session


def read_vin(obd, conn):
    """Numéro de série de la voiture (mode 09), ou None si le calculateur ne le donne pas."""
    try:
        r = conn.query(obd.commands.VIN, force=True)
    except Exception:
        return None
    if r.is_null():
        return None
    v = r.value
    if isinstance(v, (bytes, bytearray)):
        v = v.decode("ascii", "ignore")
    v = "".join(c for c in str(v) if c.isalnum())
    return v or None


def obd_loop(state, args, logger_factory):
    import obd

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
        keys = state.supported + (["boost"] if "map" in state.supported else [])
        state.start_session(f"{conn.port_name()} · {conn.protocol_name()}", read_vin(obd, conn), keys)
        state.status = "connecté (rapide)" if fast else "connecté"
        logger = logger_factory(keys)
        last_ok = time.time()

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
                    last_ok = time.time()
                    v = r.value.magnitude if hasattr(r.value, "magnitude") else r.value
                    v = float(conv(v) if conv else v)
                    state.update(key, round(v, 2))
                    if delay:
                        time.sleep(delay)
                state.derive()
                if time.time() - last_ok > MUTE_S:
                    state.status = "calculateur muet (contact coupé ?), attente…"
                    break
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
        if not state.status.startswith("calculateur muet"):
            state.status = "connexion perdue, reconnexion…"
        time.sleep(2)
