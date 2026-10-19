"""Boucle d'acquisition, lancée dans un thread : lecture de l'ECU par l'ELM327.

À chaque connexion, la voiture est reconnue (VIN, sinon type de carburant : voir voitures.py) avant de chercher
ses mesures : OBD standard, et mesures constructeur (Ford, Renault) quand le standard manque.
"""

import glob
import os
import sys
import time
import traceback

import constructeur
import demo
import requetes
import voitures

try:
    import fcntl   # verrou du port série (Linux) ; absent sous Windows, où un port COM n'est ouvert que par un seul programme
except ImportError:
    fcntl = None

WINDOWS = sys.platform == "win32"
MUTE_S = 10   # plus aucune réponse du calculateur depuis ce temps (contact coupé) : fin de la session
# L'ELM327 redémarre quand la tension de la prise OBD saute (démarreur, arrêt du moteur) : il quitte l'USB quelques
# secondes, même alimenté par l'USB. Disparu juste après une voiture qui répondait : « reconnexion », cherché toutes
# les RETRY_S secondes, pendant RECONNECT_S ; au-delà, il a vraiment été débranché
RECONNECT_S = 60
RETRY_S = 1
BAUDS = (38400, 9600, 115200, 230400, 57600)   # débits courants des ELM327 (sans effet sur les clés USB en ttyACM)
# Puces USB-série des ELM327 : CH340/CH341, FTDI, CP210x, Prolific, QBD (essayées en premier sous Windows)
USB_VIDS = (0x1A86, 0x0403, 0x10C4, 0x067B, 0x0918)


def candidates(dev):
    """Ports série où peut se trouver un ELM327.
    Linux : clés USB (ttyUSB : CH340, FTDI… ; ttyACM : USB natif) et Bluetooth (rfcomm), dans dev (/dev).
    Windows : ports COM, clés USB d'abord (un port Bluetooth peut mettre plusieurs secondes à répondre).
    python-obd ne cherche que dans ttyUSB et rfcomm."""
    if WINDOWS:
        from serial.tools import list_ports
        ports = sorted(list_ports.comports(), key=lambda p: (p.vid not in USB_VIDS, p.vid is None, p.device))
        return [p.device for p in ports]
    ports = [os.path.realpath(p) for p in sorted(glob.glob(f"{dev}/serial/by-id/*"))]
    for pattern in ("ttyUSB[0-9]*", "ttyACM[0-9]*", "rfcomm[0-9]*"):
        ports += sorted(glob.glob(f"{dev}/{pattern}"))
    return list(dict.fromkeys(ports))   # sans doublon, dans l'ordre


def probe(port, cancel=None):
    """(débit, réponse à ATI) si un ELM327 répond sur ce port, "occupe" si un autre programme l'a ouvert
    (Windows), sinon None. Marche sans voiture branchée."""
    import serial
    for baud in BAUDS:
        if cancel and cancel():
            return None
        try:
            with serial.Serial(port, baud, timeout=0.3, write_timeout=1) as s:
                s.write(b"\r")             # vide une commande à moitié reçue (l'ELM répond « ? »)
                time.sleep(0.2)
                s.reset_input_buffer()
                s.write(b"ATI\r")
                reply, end = b"", time.time() + 1.0
                while time.time() < end and not reply.endswith(b">"):
                    reply += s.read(64)
        except Exception as e:   # OSError, SerialException, termios.error : clé débranchée ou en train de redémarrer
            if WINDOWS and "PermissionError" in str(e):   # port COM déjà ouvert ailleurs (accès refusé)
                return "occupe"
            return None
        if b"ELM" in reply or reply.rstrip().endswith(b">"):
            lines = [x.strip() for x in reply.decode("ascii", "ignore").replace(">", "").splitlines()]
            name = next((x for x in lines if "ELM" in x), "") or next((x for x in lines if x and x != "ATI"), "ELM327")
            return baud, name
    return None


def group_of(path):
    """(gid, nom) du groupe propriétaire du port (Linux) : celui à donner à l'utilisateur."""
    gid = os.stat(path).st_gid
    try:
        import grp
        return gid, grp.getgrgid(gid).gr_name
    except (ImportError, KeyError):
        return gid, None


def lock_port(port):
    """Réserve le port (verrou flock, comme pyserial exclusive=True) : un scan (uds_scan.py, obd_scan.py)
    et le dashboard ne parlent jamais à l'ELM327 en même temps. Renvoie le descripteur à garder, None si
    un autre programme a déjà réservé le port, -1 si le verrou est impossible ici (Windows, socket://…)."""
    if fcntl is None or not port.startswith("/"):
        return -1
    try:
        fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    except OSError:
        return -1   # absent ou interdit : l'erreur viendra plus loin, avec son vrai message
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except OSError:
        os.close(fd)
        return None


def release(fd):
    if fd is not None and fd >= 0:
        os.close(fd)


def find_adapter(dev, cancel=None):
    """Cherche l'ELM327 : (port, débit, nom, verrou) s'il répond, sinon (None, ports interdits, ports occupés, None).
    cancel() vrai : recherche abandonnée (mode démo, diagnostic demandés)."""
    denied, busy = [], []
    for port in candidates(dev):
        if cancel and cancel():
            break
        if not WINDOWS and not os.access(port, os.R_OK | os.W_OK):
            denied.append(port)
            continue
        fd = lock_port(port)
        if fd is None:
            busy.append(port)
            continue
        found = probe(port, cancel)
        if found == "occupe":
            busy.append(port)
        elif found:
            return port, found[0], found[1], fd
        release(fd)
    return None, denied, busy, None


def denied_message(port):
    gid, name = group_of(port)
    return (f"Attention : pas le droit d'ouvrir {port} : sudo usermod -aG {name or gid} $USER,"
            " puis se déconnecter / reconnecter (ou redémarrer)")


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


def read_fuel_type(obd, conn):
    """Type de carburant annoncé par le calculateur (01 51, ex. « Gasoline », « Bifuel running Ethanol »),
    ou None. Dit si le calculateur se déclare FlexiFuel."""
    try:
        r = conn.query(obd.commands.FUEL_TYPE, force=True)
    except Exception:
        return None
    return None if r.is_null() or not r.value else str(r.value)


def spaced(cmd):
    """b"015C" → "01 5C", b"220334" → "22 03 34" """
    return " ".join(cmd[i:i + 2] for i in range(0, len(cmd), 2))


def identify(state, obd, conn):
    """Reconnaît la voiture branchée (VIN, sinon type de carburant) et passe à elle ; renvoie le VIN."""
    vin = read_vin(obd, conn)
    state.fuel_type = read_fuel_type(obd, conn)
    found = voitures.detect(vin, state.fuel_type)
    if found:
        state.set_car(found, f"VIN {vin}" if vin and voitures.detect(vin) else f"carburant annoncé : {state.fuel_type}")
    else:
        print(f"Voiture non reconnue (VIN {vin or 'non donné'}, carburant {state.fuel_type or 'non donné'}) :"
              f" {state.car.NAME}, la dernière vue")
    return vin


def find_measures(obd, conn, car):
    """Mesures lues : OBD standard si le calculateur l'annonce, sinon l'équivalent constructeur s'il répond.
    Renvoie ([(clé, commande, conversion, lecture espacée ou None pour l'OBD standard, requête forcée)],
    clés sans réponse). Sert au dashboard et au scan des PID (scan_pid.py)."""
    cmds, missing = [], []
    for key, _lbl, _u, name, conv, *_ in car.METRICS:
        cmd = getattr(obd.commands, name, None) if name else None   # None : mesure constructeur seulement
        if cmd is not None and conn.supports(cmd):
            cmds.append((key, cmd, conv, None, False))
            continue
        if cmd is not None and key in car.FORCE_PROBE and constructeur.probe(conn, cmd):
            # Répond sans être annoncé : interrogé en forçant python-obd (qui sinon l'ignore)
            cmds.append((key, cmd, conv, None, True))
            print(f"{key} ({spaced(cmd.command.decode())}) : non annoncé par le calculateur, mais il répond")
            continue
        rcmd = constructeur.command(obd, car, key)
        if rcmd is not None and constructeur.probe(conn, rcmd):
            cmds.append((key, rcmd, None, constructeur.Slow(car.FALLBACK[key][4]), True))
        else:
            missing.append(key)
    return cmds, missing


def close_conn(state, conn):
    """Ferme la connexion python-obd. Pour un diagnostic, sans son ATZ de fermeture : certaines clés USB
    redémarrent sur ATZ et se déconnectent (ttyACM qui disparaît, parfois sans revenir avant d'être rebranchées)."""
    if state.diag_want.is_set():
        try:
            conn.interface._ELM327__port.close()   # port série de python-obd, fermé tel quel
            conn.interface._ELM327__port = None
        except (AttributeError, OSError):
            pass
    conn.close()


def obd_loop(state, args, logger_factory):
    """Lit l'ECU sans fin ; une erreur imprévue est notée au journal et la connexion reprend."""
    while True:
        try:
            _obd_loop(state, args, logger_factory)
        except Exception as e:
            traceback.print_exc()
            state.status = f"erreur ({e}), nouvel essai dans 5 s"
            state.adapter = "absent"
            state.elm, state.ecu = "ko", "off"
            time.sleep(5)


def _demo_on(state):
    return state.get_settings().get("demo", False)


def _busy(state):
    """L'adaptateur est demandé ailleurs : mode démo, ou diagnostic (onglet Diagnostic)."""
    return _demo_on(state) or state.diag_want.is_set()


def _pause(state, seconds):
    """Attente entre deux essais, écourtée si le mode démo ou un diagnostic est demandé."""
    end = time.time() + seconds
    while time.time() < end and not _busy(state):
        time.sleep(0.2)


def _lend_to_diag(state):
    """Laisse l'adaptateur au diagnostic (port fermé et verrou relâché) jusqu'à ce qu'il ait fini."""
    state.status = "diagnostic en cours (onglet Diagnostic), enregistrement en pause"
    state.adapter = "absent"
    state.elm, state.ecu = "diag", "diag"
    state.port_free.set()
    while state.diag_want.is_set():
        time.sleep(0.2)
    state.port_free.clear()
    state.elm, state.ecu = "recherche", "off"


def _obd_loop(state, args, logger_factory):
    held = []   # verrou du port tenu pendant l'essai en cours (relâché à chaque tour, et en cas d'erreur)
    try:
        _obd_loop_locked(state, args, logger_factory, held)
    finally:
        for fd in held:
            release(fd)


def _obd_loop_locked(state, args, logger_factory, held):
    import obd

    seen = None   # dernier message d'adaptateur écrit au journal (pas de répétition toutes les 5 s)
    while True:
        while held:
            release(held.pop())
        if state.diag_want.is_set():
            _lend_to_diag(state)
            continue
        if _demo_on(state):   # mode démo (onglet Réglages) : l'ELM327 est laissé de côté
            state.adapter = "absent"
            state.elm, state.ecu, state.protocol = "demo", "demo", None
            state.clear_live()
            car = state.car   # autre voiture choisie dans Réglages : la démo repart avec elle
            demo.run(state, lambda: _demo_on(state) and state.car is car)
            state.clear_live()
            state.elm, state.ecu = "recherche", "off"
            continue
        port, baud, busy = args.port, args.baud, []
        if port and os.path.exists(port) and not os.access(port, os.R_OK | os.W_OK):
            # Port série présent mais interdit : python-obd dirait juste « Not Connected »
            denied = [port]
        elif not port and (WINDOWS or os.path.isdir(args.dev)):
            # Détection automatique : ports COM (Windows) ; ttyUSB, ttyACM, rfcomm (Linux) ; dont l'ELM327 répond à ATI
            if state.status == "démarrage":   # premier essai : quelques secondes par port qui ne répond pas
                state.status = "recherche de l'adaptateur…"
            port, found, name, fd = find_adapter(args.dev, lambda: _busy(state))
            denied, busy = ([], []) if port else (found, name)
            if port:
                state.lost_ms = None
                held.append(fd)
                baud = baud or found
                state.elm, state.elm_name, state.port = "ok", name, port
                if (port, name) != seen:
                    print(f"Adaptateur trouvé : {name} sur {port}")
                    seen = (port, name)
            elif not denied and not busy and state.lost_ms and time.time() * 1000 - state.lost_ms < RECONNECT_S * 1000:
                # Disparu juste après une voiture qui répondait : il redémarre (moteur coupé ou démarré)
                state.source = "auto"
                state.status = "l'adaptateur redémarre (tension de la prise OBD au démarrage ou à l'arrêt du moteur), reconnexion…"
                state.adapter = "reconnexion"
                state.elm, state.ecu, state.port, state.elm_name = "reconnexion", "off", None, None
                if seen != "reconnexion":
                    print("Adaptateur parti de l'USB (redémarrage au démarrage ou à l'arrêt du moteur) : reconnexion…")
                    seen = "reconnexion"
                _pause(state, RETRY_S)
                continue
            elif not denied and not busy:
                where = "les ports COM" if WINDOWS else args.dev
                state.source = "auto"
                state.status = f"adaptateur introuvable (aucun ELM327 dans {where}), nouvel essai dans 5 s"
                state.adapter = "absent"
                state.elm, state.ecu, state.port, state.elm_name = "absent", "off", None, None
                if seen != "absent":
                    print(f"En attente de l'adaptateur : aucun ELM327 ne répond dans "
                          f"{where if WINDOWS else where + ' (ttyUSB, ttyACM, rfcomm)'}."
                          " Brancher la clé, ou ADAPTATEUR=… dans car.conf.")
                    seen = "absent"
                _pause(state, 5)
                continue
        else:
            denied = []
            fd = lock_port(port) if port else -1
            if fd is None:
                busy = [port]
            else:
                held.append(fd)
        if busy and not denied:
            state.source = busy[0]
            state.status = f"{busy[0]} utilisé par un autre programme, nouvel essai dans 5 s"
            state.adapter = "absent"
            state.elm, state.ecu, state.port = "occupe", "off", busy[0]
            msg = f"Adaptateur {busy[0]} réservé par un autre programme (scan ?) : en attente qu'il le libère"
            if msg != seen:
                print(msg)
                seen = msg
            _pause(state, 5)
            continue
        if denied:
            state.source = denied[0]
            state.status = f"pas le droit d'ouvrir {denied[0]}"
            state.adapter = "absent"
            state.elm, state.ecu, state.port = "refuse", "off", denied[0]
            msg = denied_message(denied[0])
            if msg != seen:
                print(msg)
                seen = msg
            _pause(state, 5)
            continue
        state.source = port or "auto"
        fast = state.get_settings()["fast"]
        state.status = "connexion (mode rapide)…" if fast else "connexion…"
        state.port = port
        if state.elm == "ok" and state.ecu in ("off", "ok"):
            state.ecu = "recherche"   # un calculateur déjà muet le reste jusqu'à sa réponse (pas de clignotement)
        state.baud = baud   # repris par le diagnostic avec le port (diagnostic.py)
        conn = obd.OBD(portstr=port, baudrate=baud, fast=fast, timeout=1)
        state.adapter = {obd.OBDStatus.CAR_CONNECTED: "car", obd.OBDStatus.ELM_CONNECTED: "elm",
                         obd.OBDStatus.OBD_CONNECTED: "elm"}.get(conn.status(), "absent")
        if state.adapter == "absent" and state.lost_ms and time.time() * 1000 - state.lost_ms < RECONNECT_S * 1000:
            # Port donné (car.conf, tablette Android) mais plus d'adaptateur derrière, juste après une voiture qui
            # répondait : il redémarre (moteur coupé ou démarré)
            state.adapter = "reconnexion"
            state.elm, state.ecu = "reconnexion", "off"
        elif state.adapter == "absent":
            # Port trouvé par ATI mais l'initialisation a échoué ; port donné dans car.conf : rien à cet endroit
            state.elm, state.ecu = ("ko" if state.elm_name else "absent"), "off"
        else:
            state.elm = "ok"
            state.port = conn.port_name() or port
            state.ecu = "ok" if state.adapter == "car" else "muet"
            state.lost_ms = None
        if conn.status() != obd.OBDStatus.CAR_CONNECTED or _busy(state):
            if state.adapter == "reconnexion":
                state.status = "l'adaptateur redémarre (tension de la prise OBD au démarrage ou à l'arrêt du moteur), reconnexion…"
            else:
                state.status = (f"contact coupé : l'adaptateur répond, pas le calculateur ({conn.status()})"
                                if state.adapter == "elm" else f"pas de réponse ECU ({conn.status()}), nouvel essai dans 5 s")
            close_conn(state, conn)
            _pause(state, RETRY_S if state.adapter == "reconnexion" else 5)
            continue

        state.protocol = conn.protocol_name()
        vin = identify(state, obd, conn)
        car = state.car
        cmds, unsupported = find_measures(obd, conn, car)
        state.unsupported = unsupported
        state.supported = [c[0] for c in cmds]
        state.sources = {c[0]: {"kind": "maker", "cmd": f"22 {car.FALLBACK[c[0]][0]}"} if c[3] is not None else
                         {"kind": "obd", "cmd": spaced(c[1].command.decode())} for c in cmds}
        state.raw_keys = [c[0] for c in cmds if c[3] is not None]
        extra = []
        for key in state.raw_keys:
            validated = state.calib is None or state.calib.get(key)[2]
            extra.append(f"{key} (22 {car.FALLBACK[key][0]}{'' if validated else ', à étalonner'})")
        if extra:
            print(f"Mesures {car.MAKER} lues en plus de l'OBD standard : " + ", ".join(extra))
        keys = state.supported + (["boost"] if "map" in state.supported else []) + [f"brut.{k}" for k in state.raw_keys]
        print(f"Type de carburant annoncé (01 51) : {state.fuel_type or 'non donné'}"
              + ("" if "ethanol" in state.supported or "ethanol" not in car.FORCE_PROBE
                 else " ; taux d'éthanol (01 52) : pas de réponse"))
        state.start_session(f"{conn.port_name()} · {conn.protocol_name()}", vin, keys)
        state.status = "connecté (rapide)" if fast else "connecté"
        reader = requetes.Reader(conn, cmds, car, requetes.load_scan(vin, car))
        notes = reader.setup()
        print("Lecture : " + (", ".join(notes) if notes else "une requête par mesure (rien de plus rapide accepté)"))
        logger = logger_factory(keys)
        last_ok = time.time()

        try:
            while conn.is_connected():
                if state.get_settings()["fast"] != fast or _busy(state):
                    break  # mode rapide basculé (reconnexion avec le nouveau réglage), mode démo ou diagnostic
                t0 = time.time()
                if reader.tick(state, state.get_settings()["delay"]):
                    last_ok = time.time()
                state.ping_ms = reader.ping_ms
                state.derive()
                if time.time() - last_ok > MUTE_S:
                    state.status = "calculateur muet (contact coupé ?), attente…"
                    state.adapter = "elm"
                    state.ecu = "muet"
                    break
                # Bride la boucle à max_hz (l'ELM327 est souvent plus lent de toute façon)
                time.sleep(max(0.0, 1.0 / state.get_settings()["max_hz"] - (time.time() - t0)))
                state.hz = 1.0 / max(time.time() - t0, 1e-3)
                if logger:
                    logger.maybe_write(state)
        finally:
            if logger:
                logger.close()
            close_conn(state, conn)
        if state.get_settings()["fast"] != fast or _busy(state):
            continue
        if not state.status.startswith("calculateur muet"):
            # Connexion perdue en pleine lecture : l'adaptateur a quitté l'USB (il redémarre au démarrage ou à
            # l'arrêt du moteur) ou a été débranché
            state.status = "connexion perdue, reconnexion…"
            state.adapter = "reconnexion"
            state.elm, state.ecu = "reconnexion", "off"
            state.lost_ms = time.time() * 1000
            print("Adaptateur perdu en pleine lecture (redémarrage au démarrage ou à l'arrêt du moteur ?) : reconnexion…")
        time.sleep(RETRY_S)
