"""Boucle d'acquisition, lancée dans un thread : lecture de l'ECU par l'ELM327."""

import os
import time
import traceback

import demo
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
    """Lit l'ECU sans fin ; une erreur imprévue est notée au journal et la connexion reprend."""
    while True:
        try:
            _obd_loop(state, args, logger_factory)
        except Exception as e:
            traceback.print_exc()
            state.status = f"erreur ({e}), nouvel essai dans 5 s"
            state.adapter = "absent"
            time.sleep(5)


def _demo_on(state):
    return state.get_settings().get("demo", False)


def _pause(state, seconds):
    """Attente entre deux essais, écourtée si le mode démo est activé."""
    end = time.time() + seconds
    while time.time() < end and not _demo_on(state):
        time.sleep(0.2)


def _obd_loop(state, args, logger_factory):
    import obd

    warned = False
    while True:
        if _demo_on(state):   # mode démo (onglet Paramètres) : l'ELM327 est laissé de côté
            state.adapter = "absent"
            state.clear_live()
            demo.run(state, lambda: _demo_on(state))
            state.clear_live()
            continue
        state.source = args.port or "auto"
        # Port série présent mais interdit : python-obd dirait juste « Not Connected »
        if args.port and os.path.exists(args.port) and not os.access(args.port, os.R_OK | os.W_OK):
            state.status = f"pas le droit d'ouvrir {args.port}"
            state.adapter = "absent"
            if not warned:
                print(f"Attention : pas le droit d'ouvrir {args.port}. Docker : OBD_GID=<groupe du port> dans clio.conf"
                      " (stat -c %g /dev/ttyUSB0) ; sinon ajouter l'utilisateur au groupe du port (uucp sous Arch,"
                      " dialout ailleurs).")
                warned = True
            _pause(state, 5)
            continue
        fast = state.get_settings()["fast"]
        state.status = "connexion (mode rapide)…" if fast else "connexion…"
        conn = obd.OBD(portstr=args.port, baudrate=args.baud, fast=fast, timeout=1)
        state.adapter = {obd.OBDStatus.CAR_CONNECTED: "car", obd.OBDStatus.ELM_CONNECTED: "elm",
                         obd.OBDStatus.OBD_CONNECTED: "elm"}.get(conn.status(), "absent")
        if conn.status() != obd.OBDStatus.CAR_CONNECTED or _demo_on(state):
            state.status = f"pas de réponse ECU ({conn.status()}), nouvel essai dans 5 s"
            conn.close()
            _pause(state, 5)
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
                if state.get_settings()["fast"] != fast or _demo_on(state):
                    break  # mode rapide basculé (reconnexion avec le nouveau réglage) ou mode démo
                t0 = time.time()
                delay = state.get_settings()["delay"]
                for key, cmd, conv in cmds:
                    r = conn.query(cmd)
                    if r.is_null():
                        state.update(key, None)
                        continue
                    last_ok = time.time()
                    v = r.value.magnitude if hasattr(r.value, "magnitude") else r.value
                    try:
                        v = float(conv(v) if conv else v)
                    except (TypeError, ValueError):
                        state.update(key, None)
                        continue
                    state.update(key, round(v, 2))
                    if delay:
                        time.sleep(delay)
                state.derive()
                if time.time() - last_ok > MUTE_S:
                    state.status = "calculateur muet (contact coupé ?), attente…"
                    state.adapter = "elm"
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
        if state.get_settings()["fast"] != fast or _demo_on(state):
            continue
        if not state.status.startswith("calculateur muet"):
            state.status = "connexion perdue, reconnexion…"
            state.adapter = "absent"
        time.sleep(2)
