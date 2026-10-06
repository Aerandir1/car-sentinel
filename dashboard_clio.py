#!/usr/bin/env python3
"""Dashboard OBD-II temps réel pour Clio 4 (ELM327 + python-obd).

Lit l'ECU en boucle dans un thread et sert dashboard.html + une API JSON
sur https://127.0.0.1:8080. Aucune dépendance web : tout marche hors ligne.

    python dashboard_clio.py --demo                       # simulation, sans voiture
    python dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv
"""

import argparse
import csv
import json
import math
import os
import random
import shutil
import socket
import ssl
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
BARO_DEFAULT_KPA = 101.3
MODELS = HERE / "models"
LOGOS = HERE / "logo"
CERTS = HERE / "certs"
MODEL_FILES = ("clio.glb", "clio/scene.gltf")   # le premier trouvé remplace la Clio dessinée en code
STATIC_TYPES = {".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}
MODEL_TYPES = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".bin": "application/octet-stream",
               ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}

# key, libellé, unité, commande python-obd, conversion, min/max d'affichage,
# seuil d'alerte (rouge au-delà), seuil "froid" (bleu en dessous)
METRICS = [
    ("rpm",      "Régime",           "tr/min", "RPM",                       None,              (0, 5000),   4500, None),
    ("speed",    "Vitesse",          "km/h",   "SPEED",                     None,              (0, 200),    None, None),
    ("coolant",  "Temp. eau",        "°C",     "COOLANT_TEMP",              None,              (-20, 130),  105,  70),
    ("oil",      "Temp. huile",      "°C",     "OIL_TEMP",                  None,              (-20, 150),  125,  70),
    ("intake",   "Temp. admission",  "°C",     "INTAKE_TEMP",               None,              (-20, 90),   60,   None),
    ("map",      "Pression admission", "kPa",  "INTAKE_PRESSURE",           None,              (0, 300),    None, None),
    ("baro",     "Pression atmo",    "kPa",    "BAROMETRIC_PRESSURE",       None,              (70, 110),   None, None),
    ("load",     "Charge moteur",    "%",      "ENGINE_LOAD",               None,              (0, 100),    None, None),
    ("pedal",    "Pédale accél.",    "%",      "ACCELERATOR_POS_D",         None,              (0, 100),    None, None),
    ("throttle", "Volet d'admission", "%",     "THROTTLE_POS",              None,              (0, 100),    None, None),
    ("maf",      "Débit d'air",      "g/s",    "MAF",                       None,              (0, 150),    None, None),
    ("rail",     "Pression rail",    "bar",    "FUEL_RAIL_PRESSURE_DIRECT", lambda v: v / 100, (0, 1800),   None, None),
    ("fuel",     "Conso. carburant", "L/h",    "FUEL_RATE",                 None,              (0, 20),     None, None),
    ("egr",      "EGR commandée",    "%",      "COMMANDED_EGR",             None,              (0, 100),    None, None),
    ("egr_err",  "Écart EGR",        "%",      "EGR_ERROR",                 None,              (-100, 100), None, None),
    ("ambient",  "Temp. extérieure", "°C",     "AMBIANT_AIR_TEMP",          None,              (-30, 50),   None, None),
    ("voltage",  "Tension batterie", "V",      "CONTROL_MODULE_VOLTAGE",    None,              (10, 16),    15.2, None),
]

# Valeur calculée : suralimentation = MAP - pression atmosphérique (en bar)
DERIVED = {"key": "boost", "label": "Suralimentation", "unit": "bar", "range": (-0.2, 1.6)}

# Réglages modifiables à chaud depuis l'onglet Paramètres : clé -> (min, max)
SETTINGS_LIMITS = {
    "max_hz": (0.5, 50.0),   # fréquence max de la boucle d'acquisition (tick de la démo)
    "delay": (0.0, 1.0),     # pause en s entre deux requêtes OBD
    "log_hz": (0.2, 50.0),   # fréquence du CSV
}
# Mode rapide python-obd : pas d'écho, nombre de réponses attendu → requêtes plus courtes.
# Certains clones d'ELM327 le gèrent mal. Changer ce réglage relance la connexion.
SETTINGS_FLAGS = ("fast",)


class State:
    """Dernières valeurs + min/max, partagé entre le thread de lecture et le serveur HTTP."""

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.mins = {}
        self.maxs = {}
        self.supported = []
        self.unsupported = []
        self.status = "démarrage"
        self.source = ""
        self.hz = 0.0
        self.settings = {}

    def get_settings(self):
        with self.lock:
            return dict(self.settings)

    def set_settings(self, new):
        with self.lock:
            for key, (lo, hi) in SETTINGS_LIMITS.items():
                if key in new:
                    self.settings[key] = min(hi, max(lo, float(new[key])))
            for key in SETTINGS_FLAGS:
                if key in new:
                    self.settings[key] = bool(new[key])
            return dict(self.settings)

    def update(self, key, value):
        with self.lock:
            self.values[key] = value
            if value is None:
                return
            self.mins[key] = min(self.mins.get(key, value), value)
            self.maxs[key] = max(self.maxs.get(key, value), value)

    def derive(self):
        with self.lock:
            m = self.values.get("map")
            baro = self.values.get("baro") or BARO_DEFAULT_KPA
        if m is not None:
            self.update("boost", round((m - baro) / 100, 3))

    def reset_minmax(self):
        with self.lock:
            self.mins.clear()
            self.maxs.clear()

    def snapshot(self):
        with self.lock:
            return {
                "t": time.time(),
                "status": self.status,
                "hz": round(self.hz, 1),
                "values": dict(self.values),
                "min": dict(self.mins),
                "max": dict(self.maxs),
            }

    def meta(self):
        with self.lock:
            metrics = [
                {"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold}
                for k, lbl, u, _cmd, _conv, rng, warn, cold in METRICS
                if k in self.supported
            ]
            if "map" in self.supported:
                metrics.append({**DERIVED, "warn": None, "cold": None})
            return {
                "source": self.source,
                "metrics": metrics,
                "unsupported": [lbl for k, lbl, *_ in METRICS if k in self.unsupported],
            }


class Gps:
    """Dernière position GPS, remplie par gps_loop, lue par /api/gps."""

    def __init__(self):
        self.lock = threading.Lock()
        self.data = {"status": "pas de GPS", "source": None, "fix": False}
        self.t = 0.0

    def set(self, **kw):
        with self.lock:
            self.data.update(kw)
            if kw.get("fix"):
                self.t = time.time()

    def snapshot(self):
        with self.lock:
            d = dict(self.data)
            age = time.time() - self.t if self.t else None
        d["age"] = round(age, 1) if age is not None else None
        if age is None or age > 5:      # position trop vieille : on ne s'en sert plus pour guider
            d["fix"] = False
        return d


def nmea_coord(value, hemi):
    """ddmm.mmmm (ou dddmm.mmmm) + N/S/E/W → degrés décimaux."""
    if not value:
        return None
    dot = value.index(".")
    deg = float(value[:dot - 2]) + float(value[dot - 2:]) / 60
    return -deg if hemi in ("S", "W") else deg


def nmea_line(gps, line):
    """Lit une phrase NMEA ($GPRMC, $GNGGA…) et met à jour la position."""
    line = line.strip()
    if not line.startswith("$") or "*" not in line:
        return
    body, _, check = line[1:].partition("*")
    calc = 0
    for ch in body:
        calc ^= ord(ch)
    if f"{calc:02X}" != check[:2].upper():
        return
    f = body.split(",")
    kind = f[0][2:]
    try:
        if kind == "RMC" and len(f) > 8:
            if f[2] != "A":
                gps.set(fix=False, status="recherche des satellites…")
                return
            gps.set(fix=True, status="ok", lat=nmea_coord(f[3], f[4]), lon=nmea_coord(f[5], f[6]),
                    speed=round(float(f[7] or 0) * 1.852, 1), heading=float(f[8]) if f[8] else None)
        elif kind == "GGA" and len(f) > 9:
            gps.set(sats=int(f[7] or 0), alt=float(f[9]) if f[9] else None)
    except ValueError:
        pass


def open_serial(path):
    """Port série du récepteur en mode brut, 9600 bauds (sans effet sur les clés USB ttyACM)."""
    import termios
    fd = os.open(path, os.O_RDONLY | os.O_NOCTTY)
    attrs = termios.tcgetattr(fd)
    attrs[0] = attrs[1] = attrs[3] = 0                      # iflag, oflag, lflag : brut
    attrs[2] = termios.CS8 | termios.CREAD | termios.CLOCAL  # cflag
    attrs[4] = attrs[5] = termios.B9600
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    return os.fdopen(fd, "rb", buffering=0)


def gps_loop(gps, spec):
    """Lit le GPS en continu et se reconnecte en cas de coupure.

    spec : /dev/ttyACM0 (clé USB), tcp:172.20.10.1:11123 (iPhone avec GPS2IP), gpsd ou gpsd:hôte:port.
    """
    if spec.startswith("gpsd"):
        _, host, port = (spec.split(":") + ["127.0.0.1", "2947"])[:3]
        source, label = "gpsd", f"gpsd {host}:{port}"
    elif spec.startswith("tcp:"):
        _, host, port = spec.split(":")
        source, label = "iphone", f"{host}:{port}"
    else:
        source, label = "usb", spec
    gps.set(source=source)
    while True:
        gps.set(fix=False, status=f"connexion à {label}…")
        try:
            if source == "usb":
                stream = open_serial(spec)
            else:
                sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=10)
                sock.settimeout(10)
                if source == "gpsd":
                    sock.sendall(b'?WATCH={"enable":true,"json":true}\n')
                stream = sock.makefile("rb")
            gps.set(status="recherche des satellites…")
            for raw in stream:
                line = raw.decode("ascii", "replace")
                if source != "gpsd":
                    nmea_line(gps, line)
                    continue
                msg = json.loads(line)
                if msg.get("class") == "TPV":
                    if msg.get("mode", 0) >= 2 and "lat" in msg:
                        gps.set(fix=True, status="ok", lat=msg["lat"], lon=msg["lon"],
                                speed=round(msg.get("speed", 0) * 3.6, 1), heading=msg.get("track"), alt=msg.get("altHAE", msg.get("alt")))
                    else:
                        gps.set(fix=False, status="recherche des satellites…")
                elif msg.get("class") == "SKY":
                    used = msg.get("uSat") or sum(1 for sat in msg.get("satellites", []) if sat.get("used"))
                    gps.set(sats=used)
            gps.set(fix=False, status=f"{label} : flux coupé, reconnexion…")
        except (OSError, ValueError) as e:
            gps.set(fix=False, status=f"{label} indisponible ({e.strerror or e}), nouvel essai…")
        time.sleep(3)


class CsvLogger:
    def __init__(self, path, keys):
        self.keys = keys
        self.next_t = 0.0
        self.f = open(path, "w", newline="")
        self.w = csv.writer(self.f)
        self.w.writerow(["timestamp"] + keys)

    def maybe_write(self, state):
        now = time.time()
        if now < self.next_t:
            return
        self.next_t = now + 1.0 / state.get_settings()["log_hz"]
        snap = state.snapshot()["values"]
        self.w.writerow([f"{now:.3f}"] + [snap.get(k, "") for k in self.keys])
        self.f.flush()

    def close(self):
        self.f.close()


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
        state.source = f"{conn.port_name()} · {conn.protocol_name()}"
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
    state.source = "simulation dCi 90"
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


def ensure_cert():
    """Certificat HTTPS pour 127.0.0.1 et localhost, créé au premier lancement dans certs/.

    Avec mkcert installé, le certificat est signé par l'autorité locale de mkcert : le navigateur
    l'accepte sans avertissement. Sinon, certificat auto-signé OpenSSL (avertissement à accepter).
    """
    CERTS.mkdir(exist_ok=True)
    names = ["127.0.0.1", "localhost", "::1"]
    if shutil.which("mkcert"):
        crt, key = CERTS / "mkcert.pem", CERTS / "mkcert-key.pem"
        if not (crt.is_file() and key.is_file()):
            subprocess.run(["mkcert", "-cert-file", str(crt), "-key-file", str(key), *names], check=True, capture_output=True)
        return crt, key, True
    crt, key = CERTS / "selfsigned.pem", CERTS / "selfsigned-key.pem"
    if not (crt.is_file() and key.is_file()):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                        "-subj", "/CN=Dashboard Clio", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost,IP:::1",
                        "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)
    key.chmod(0o600)
    return crt, key, False


class QuietServer(ThreadingHTTPServer):
    """Ignore les connexions coupées et les clients qui parlent HTTP au port HTTPS."""

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_handler(state, gps, scheme):
    pages = {"/": "accueil.html", "/index.html": "accueil.html", "/dashboard": "dashboard.html",
             "/autoradio": "autoradio.html", "/spotify-callback": "spotify-callback.html"}
    vendor = HERE / "vendor"
    scripts = {"/navigation.js": HERE / "navigation.js"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Une seule origine (127.0.0.1) : Spotify n'accepte que cette adresse en retour de connexion,
            # et les réglages du navigateur (localStorage, accès USB) sont rangés par origine.
            host = self.headers.get("Host", "")
            if host.startswith("localhost"):
                self.send_response(302)
                self.send_header("Location", f"{scheme}://127.0.0.1{host[9:]}{self.path}")
                self.end_headers()
                return
            path = urlsplit(self.path).path
            if path in pages:
                self._send(200, (HERE / pages[path]).read_bytes(), "text/html; charset=utf-8")
            elif path in scripts:
                self._send(200, scripts[path].read_bytes(), "text/javascript")
            elif path.startswith("/vendor/"):
                f = (vendor / unquote(path[8:])).resolve()
                if f.is_file() and f.is_relative_to(vendor) and f.suffix in STATIC_TYPES:
                    self._send(200, f.read_bytes(), STATIC_TYPES[f.suffix])
                else:
                    self._send(404, b"not found", "text/plain")
            elif path == "/api/gps":
                self._send(200, json.dumps(gps.snapshot()).encode(), "application/json")
            elif path.startswith("/models/"):
                f = (MODELS / unquote(path[8:])).resolve()
                if f.is_file() and f.is_relative_to(MODELS) and f.suffix.lower() in MODEL_TYPES:
                    self._send(200, f.read_bytes(), MODEL_TYPES[f.suffix.lower()])
                else:
                    self._send(404, b"not found", "text/plain")
            elif path.startswith("/logo/"):
                f = (LOGOS / unquote(path[6:])).resolve()
                if f.is_file() and f.is_relative_to(LOGOS) and f.suffix.lower() == ".svg":
                    self._send(200, f.read_bytes(), "image/svg+xml")
                else:
                    self._send(404, b"not found", "text/plain")
            elif path == "/api/model":
                found = next((m for m in MODEL_FILES if (MODELS / m).is_file()), None)
                self._send(200, json.dumps({"url": found and f"/models/{found}"}).encode(), "application/json")
            elif path == "/api/state":
                self._send(200, json.dumps(state.snapshot()).encode(), "application/json")
            elif path == "/api/meta":
                self._send(200, json.dumps(state.meta()).encode(), "application/json")
            elif path == "/api/settings":
                self._send(200, json.dumps(state.get_settings()).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/api/reset":
                state.reset_minmax()
                self._send(204, b"", "text/plain")
            elif self.path == "/api/settings":
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    saved = state.set_settings(body)
                except (ValueError, TypeError, AttributeError):
                    self._send(400, b"JSON invalide", "text/plain")
                    return
                self._send(200, json.dumps(saved).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *_):
            pass

    return Handler


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="port série de l'ELM327 (auto-détection si absent)")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    p.add_argument("--demo", action="store_true", help="simule un trajet, sans voiture")
    p.add_argument("--log", metavar="FICHIER.csv", help="enregistre les mesures en CSV")
    p.add_argument("--log-hz", type=float, default=5.0, help="fréquence du CSV (défaut 5 Hz)")
    p.add_argument("--delay", type=float, default=0.0, help="pause en s entre deux requêtes OBD")
    p.add_argument("--max-hz", type=float, default=None,
                   help="fréquence max d'acquisition (défaut : 10 en démo, 20 sinon)")
    p.add_argument("--fast", action="store_true", help="mode rapide python-obd (requêtes plus courtes)")
    p.add_argument("--http", type=int, default=8080, help="port local de la page web (défaut 8080)")
    p.add_argument("--gps", metavar="SOURCE",
                   help="GPS : /dev/ttyACM0 (clé USB), tcp:172.20.10.1:11123 (iPhone + GPS2IP) ou gpsd")
    p.add_argument("--no-https", action="store_true", help="sert la page en HTTP simple (sans certificat)")
    p.add_argument("--bind", default="127.0.0.1",
                   help="adresse d'écoute (défaut 127.0.0.1 ; 0.0.0.0 dans un conteneur Docker)")
    args = p.parse_args()

    def logger_factory(keys):
        return CsvLogger(args.log, keys) if args.log else None

    state = State()
    state.set_settings({"max_hz": args.max_hz or (10.0 if args.demo else 20.0),
                        "delay": args.delay, "log_hz": args.log_hz, "fast": args.fast})
    loop = demo_loop if args.demo else obd_loop
    threading.Thread(target=loop, args=(state, args, logger_factory), daemon=True).start()
    gps = Gps()
    if args.gps:
        threading.Thread(target=gps_loop, args=(gps, args.gps), daemon=True).start()

    scheme = "http" if args.no_https else "https"
    server = QuietServer((args.bind, args.http), make_handler(state, gps, scheme))
    if not args.no_https:
        crt, key, trusted = ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        if not trusted:
            print("Certificat auto-signé : le navigateur affichera un avertissement à accepter une fois.\n"
                  "Pour l'éviter : sudo pacman -S mkcert nss && mkcert -install, puis relancer le script.")
    print(f"Dashboard : {scheme}://127.0.0.1:{args.http}  (Ctrl+C pour quitter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
