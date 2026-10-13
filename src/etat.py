"""Métriques lues sur l'ECU et état partagé entre le thread d'acquisition et le serveur HTTP.

Pour alléger ou compléter les mesures, éditer la liste METRICS.
"""

import csv
import threading
import time

import donnees

BARO_DEFAULT_KPA = 101.3

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
    "max_hz": (0.5, 50.0),   # fréquence max de la boucle d'acquisition
    "delay": (0.0, 1.0),     # pause en s entre deux requêtes OBD
    "log_hz": (0.2, 50.0),   # fréquence du CSV
}
# Mode rapide python-obd : pas d'écho, nombre de réponses attendu → requêtes plus courtes.
# Certains clones d'ELM327 le gèrent mal. Changer ce réglage relance la connexion.
# Mode démo : valeurs simulées (demo.py) à la place de l'ECU, enregistrées dans une base à part (clio-demo.db).
SETTINGS_FLAGS = ("fast", "demo")


class State:
    """Dernières valeurs + min/max, partagé entre le thread de lecture et le serveur HTTP."""

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.times = {}       # horodatage (ms UTC) de la dernière valeur de chaque métrique
        self.recorder = None       # donnees.Recorder : chaque valeur part aussi dans la base
        self.demo_recorder = None  # la même chose pour le mode démo, dans une base à part
        self.mins = {}
        self.maxs = {}
        self.supported = []
        self.unsupported = []
        self.sources = {}     # clé → requête qui donne la mesure : {"kind": "obd" | "renault", "cmd": "01 5C"}
        self.calib = None     # etalonnage.Store : gain et décalage des mesures Renault
        self.raw_keys = []    # mesures Renault qui répondent : valeur brute dans « brut.<clé> » (onglet Étalonnage)
        self.meta_rev = 0     # change quand la liste des tuiles change sans reconnexion (mesure validée)
        self.status = "démarrage"
        self.source = ""
        self.vin = None
        self.adapter = "absent"   # ELM327 : "absent", "elm" (branché, calculateur muet), "car" (calculateur répond)
        # Témoins du dashboard, plus détaillés (adapter, lui, part tel quel au serveur) :
        #   elm : "recherche", "ok", "absent" (aucun ELM327), "refuse" (port interdit),
        #         "occupe" (réservé par un autre programme, un scan par exemple), "ko" (ne répond pas bien), "demo"
        #   ecu : "off" (pas d'adaptateur), "recherche" (essai des protocoles), "ok", "muet" (contact coupé ?), "demo"
        self.elm, self.ecu = "recherche", "off"
        self.elm_name = None  # réponse à ATI, ex. « ELM327 v1.5 »
        self.port = None
        self.protocol = None  # protocole OBD du calculateur, ex. « ISO 15765-4 (CAN 11/500) »
        self.trip = None      # identifiant du trajet en cours (mis à jour par trajets.py)
        self.hz = 0.0
        self.sync = None      # dernier essai de synchro vers clio-sentinel-server (voir synchro.py)
        self.settings = {}
        # Onglet Diagnostic (diagnostic.py) : diag_want demande l'adaptateur, l'acquisition le laisse et le
        # signale par port_free, puis attend que diag_want retombe pour reprendre
        self.diag_want = threading.Event()
        self.port_free = threading.Event()
        self.diag = None      # diagnostic.Diag

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

    def update(self, key, value, t_ms=None):
        """Nouvelle valeur, horodatée à l'appel (juste après la réponse du calculateur) sauf t_ms donné."""
        t_ms = t_ms or donnees.now_ms()
        rec = self.active_recorder()
        if rec:
            rec.add(key, t_ms, value)
        with self.lock:
            self.values[key] = value
            self.times[key] = t_ms
            if value is None:
                return
            self.mins[key] = min(self.mins.get(key, value), value)
            self.maxs[key] = max(self.maxs.get(key, value), value)

    def update_raw(self, key, raw):
        """Valeur brute d'une mesure Renault : enregistrée telle quelle, et étalonnée si la mesure est validée."""
        self.update("brut." + key, raw)
        if self.calib is None:
            return
        if self.calib.get(key)[2]:
            self.update(key, None if raw is None else self.calib.apply(key, raw))

    def calib_changed(self):
        with self.lock:
            self.meta_rev += 1

    def shown(self, key):
        """Tuile affichée : mesure lue, et validée si c'est une mesure Renault."""
        if key not in self.supported:
            return False
        src = self.sources.get(key) or {}
        return src.get("kind") != "renault" or self.calib is None or self.calib.get(key)[2]

    def derive(self):
        with self.lock:
            m = self.values.get("map")
            t_map = self.times.get("map")
            baro = self.values.get("baro") or BARO_DEFAULT_KPA
        if m is not None:
            self.update("boost", round((m - baro) / 100, 3), t_map)   # même instant que la pression d'admission

    def start_session(self, source, vin, metrics):
        """Connexion au calculateur établie : nouvelle session dans la base."""
        self.source, self.vin = source, vin
        rec = self.active_recorder()
        if rec:
            rec.start_session(source, vin, metrics)

    def active_recorder(self):
        """Base où vont les mesures : celle de la démo en mode démo, la vraie sinon."""
        return self.demo_recorder if self.settings.get("demo") else self.recorder

    def clear_live(self):
        """Oublie les dernières valeurs et les min/max (entrée et sortie du mode démo)."""
        with self.lock:
            for d in (self.values, self.times, self.mins, self.maxs):
                d.clear()
            self.hz = 0.0

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
                "ts": dict(self.times),
                "trip": self.trip,
                "meta_rev": self.meta_rev,
                "sync": self.sync,
                "link": {"elm": self.elm, "ecu": self.ecu, "name": self.elm_name, "port": self.port,
                         "protocol": self.protocol, "vin": self.vin},
                "min": dict(self.mins),
                "max": dict(self.maxs),
            }

    def meta(self):
        shown = [k for k, *_ in METRICS if self.shown(k)]
        with self.lock:
            metrics = [
                {"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold,
                 "source": self.sources.get(k)}
                for k, lbl, u, _cmd, _conv, rng, warn, cold in METRICS
                if k in shown
            ]
            if "map" in self.supported:
                metrics.append({**DERIVED, "warn": None, "cold": None, "source": {"kind": "calc", "cmd": None}})
            return {
                "source": self.source,
                "vin": self.vin,
                "metrics": metrics,
                "unsupported": [lbl for k, lbl, *_ in METRICS if k in self.unsupported],
                # Mesures Renault qui répondent mais pas encore validées (onglet Étalonnage)
                "to_calibrate": [lbl for k, lbl, *_ in METRICS if k in self.raw_keys and k not in shown],
            }


class CsvLogger:
    def __init__(self, path, keys):
        self.keys = keys
        self.next_t = 0.0
        # Ajout à la fin : chaque reconnexion au calculateur reprend le même fichier
        self.f = open(path, "a", newline="")
        self.w = csv.writer(self.f)
        if self.f.tell() == 0:
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
