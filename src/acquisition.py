"""Boucle d'acquisition, lancée dans un thread : lecture de l'ECU par l'ELM327."""

import glob
import os
import time
import traceback

import demo
from etat import METRICS

MUTE_S = 10   # plus aucune réponse du calculateur depuis ce temps (contact coupé) : fin de la session
BAUDS = (38400, 9600, 115200, 230400, 57600)   # débits courants des ELM327 (sans effet sur les clés USB en ttyACM)


def candidates(dev):
    """Ports série où peut se trouver un ELM327 : clés USB (ttyUSB : CH340, FTDI… ; ttyACM : USB natif) et
    Bluetooth (rfcomm). python-obd ne cherche que dans ttyUSB et rfcomm."""
    ports = [os.path.realpath(p) for p in sorted(glob.glob(f"{dev}/serial/by-id/*"))]
    for pattern in ("ttyUSB[0-9]*", "ttyACM[0-9]*", "rfcomm[0-9]*"):
        ports += sorted(glob.glob(f"{dev}/{pattern}"))
    return list(dict.fromkeys(ports))   # sans doublon, dans l'ordre


def probe(port):
    """(débit, réponse à ATI) si un ELM327 répond sur ce port, sinon None. Marche sans voiture branchée."""
    import serial
    for baud in BAUDS:
        try:
            with serial.Serial(port, baud, timeout=0.3, write_timeout=1) as s:
                s.write(b"\r")             # vide une commande à moitié reçue (l'ELM répond « ? »)
                time.sleep(0.2)
                s.reset_input_buffer()
                s.write(b"ATI\r")
                reply, end = b"", time.time() + 1.0
                while time.time() < end and not reply.endswith(b">"):
                    reply += s.read(64)
        except (OSError, serial.SerialException):
            return None
        if b"ELM" in reply or reply.rstrip().endswith(b">"):
            lines = [x.strip() for x in reply.decode("ascii", "ignore").replace(">", "").splitlines()]
            name = next((x for x in lines if "ELM" in x), "") or next((x for x in lines if x and x != "ATI"), "ELM327")
            return baud, name
    return None


def group_of(path):
    """(gid, nom) du groupe propriétaire du port : à donner à OBD_GID (Docker) ou à l'utilisateur."""
    gid = os.stat(path).st_gid
    try:
        import grp
        return gid, grp.getgrgid(gid).gr_name
    except (ImportError, KeyError):   # Windows, ou groupe de l'hôte inconnu dans le conteneur
        return gid, None


def find_adapter(dev):
    """Cherche l'ELM327 : (port, débit, nom) s'il répond, sinon (None, ports interdits, None)."""
    denied = []
    for port in candidates(dev):
        if not os.access(port, os.R_OK | os.W_OK):
            denied.append(port)
            continue
        found = probe(port)
        if found:
            return port, found[0], found[1]
    return None, denied, None


def denied_message(port, in_docker):
    gid, name = group_of(port)
    if in_docker:
        return (f"Attention : pas le droit d'ouvrir {port} (groupe {gid} sur l'hôte) : OBD_GID={gid} dans clio.conf,"
                " puis docker compose --env-file clio.conf up -d")
    return (f"Attention : pas le droit d'ouvrir {port} : sudo usermod -aG {name or gid} $USER,"
            " puis se déconnecter / reconnecter (ou redémarrer)")


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

    seen = None   # dernier message d'adaptateur écrit au journal (pas de répétition toutes les 5 s)
    while True:
        if _demo_on(state):   # mode démo (onglet Paramètres) : l'ELM327 est laissé de côté
            state.adapter = "absent"
            state.clear_live()
            demo.run(state, lambda: _demo_on(state))
            state.clear_live()
            continue
        port, baud = args.port, args.baud
        if port and os.path.exists(port) and not os.access(port, os.R_OK | os.W_OK):
            # Port série présent mais interdit : python-obd dirait juste « Not Connected »
            denied = [port]
        elif not port and os.path.isdir(args.dev):
            # Détection automatique (Linux, Docker) : ttyUSB, ttyACM, rfcomm, dont l'ELM327 répond à ATI
            port, found, name = find_adapter(args.dev)
            denied = [] if port else found
            if port:
                baud = baud or found
                if (port, name) != seen:
                    print(f"Adaptateur trouvé : {name} sur {port}")
                    seen = (port, name)
            elif not denied:
                state.source = "auto"
                state.status = f"adaptateur introuvable (aucun ELM327 dans {args.dev}), nouvel essai dans 5 s"
                state.adapter = "absent"
                if seen != "absent":
                    print(f"En attente de l'adaptateur : aucun ELM327 ne répond dans {args.dev}"
                          " (ttyUSB, ttyACM, rfcomm). Brancher la clé, ou ADAPTATEUR=… dans clio.conf.")
                    seen = "absent"
                _pause(state, 5)
                continue
        else:
            denied = []
        if denied:
            state.source = denied[0]
            state.status = f"pas le droit d'ouvrir {denied[0]}"
            state.adapter = "absent"
            msg = denied_message(denied[0], args.dev != "/dev")
            if msg != seen:
                print(msg)
                seen = msg
            _pause(state, 5)
            continue
        state.source = port or "auto"
        fast = state.get_settings()["fast"]
        state.status = "connexion (mode rapide)…" if fast else "connexion…"
        conn = obd.OBD(portstr=port, baudrate=baud, fast=fast, timeout=1)
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
