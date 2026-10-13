"""Métriques lues sur l'ECU et état partagé entre le thread d'acquisition et le serveur HTTP.

Pour alléger ou compléter les mesures, éditer la liste METRICS.
"""

import csv
import threading
import time

import donnees

BARO_DEFAULT_KPA = 101.3

# key, libellé, unité, commande python-obd (None : mesure Ford seulement, voir ford.py), conversion,
# min/max d'affichage, seuil d'alerte (rouge au-delà), seuil "froid" (bleu en dessous)
# Ford Fiesta VII (2022) 1.0 EcoBoost 95 FlexiFuel : 3 cylindres essence, turbo, injection directe, E85.
METRICS = [
    ("rpm",      "Régime",           "tr/min", "RPM",                       None,              (0, 7000),   6300, None),
    ("speed",    "Vitesse",          "km/h",   "SPEED",                     None,              (0, 200),    None, None),
    ("gear",     "Rapport",          "",       None,                        None,              (0, 6),      None, None),
    ("coolant",  "Temp. eau",        "°C",     "COOLANT_TEMP",              None,              (-20, 130),  110,  70),
    ("cht",      "Temp. culasse",    "°C",     None,                        None,              (-20, 160),  125,  70),
    ("oil",      "Temp. huile",      "°C",     "OIL_TEMP",                  None,              (-20, 150),  130,  70),
    ("intake",   "Temp. admission",  "°C",     "INTAKE_TEMP",               None,              (-20, 90),   60,   None),
    ("cac",      "Temp. air suralim.", "°C",   None,                        None,              (-20, 100),  65,   None),
    ("cat",      "Temp. catalyseur", "°C",     "CATALYST_TEMP_B1S1",        None,              (0, 1000),   900,  300),
    ("ambient",  "Temp. extérieure", "°C",     "AMBIANT_AIR_TEMP",          None,              (-30, 50),   None, None),
    ("map",      "Pression admission", "kPa",  "INTAKE_PRESSURE",           None,              (0, 250),    None, None),
    ("tip",      "Pression turbo",   "kPa",    None,                        None,              (50, 250),   None, None),
    ("baro",     "Pression atmo",    "kPa",    "BAROMETRIC_PRESSURE",       None,              (70, 110),   None, None),
    ("wgdc",     "Wastegate",        "%",      None,                        None,              (0, 100),    None, None),
    ("load",     "Charge moteur",    "%",      "ENGINE_LOAD",               None,              (0, 100),    None, None),
    ("pedal",    "Pédale accél.",    "%",      "ACCELERATOR_POS_D",         None,              (0, 100),    None, None),
    ("throttle", "Papillon",         "%",      "THROTTLE_POS",              None,              (0, 100),    None, None),
    ("maf",      "Débit d'air",      "g/s",    "MAF",                       None,              (0, 100),    None, None),
    ("ethanol",  "Taux d'éthanol",   "%",      "ETHANOL_PERCENT",           None,              (0, 100),    None, None),
    ("fuel",     "Conso. carburant", "L/h",    "FUEL_RATE",                 None,              (0, 25),     None, None),
    ("fuel_lvl", "Niveau carburant", "%",      "FUEL_LEVEL",                None,              (0, 100),    None, None),
    ("rail",     "Pression rail",    "bar",    "FUEL_RAIL_PRESSURE_DIRECT", lambda v: v / 100, (0, 250),    None, None),
    ("rail_des", "Consigne rail",    "bar",    None,                        None,              (0, 250),    None, None),
    ("lambda",   "Richesse (λ cmd)", "λ",      "COMMANDED_EQUIV_RATIO",     None,              (0.7, 1.3),  None, None),
    ("stft",     "Correction court terme", "%", "SHORT_FUEL_TRIM_1",        None,              (-25, 25),   None, None),
    ("ltft",     "Correction long terme", "%", "LONG_FUEL_TRIM_1",          None,              (-25, 25),   None, None),
    ("timing",   "Avance allumage",  "°",      "TIMING_ADVANCE",            None,              (-20, 50),   None, None),
    ("ign_corr", "Retrait cliquetis cyl. 1", "°", None,                     None,              (-15, 5),    None, None),
    ("octane",   "Adaptation octane", "%",     None,                        None,              (-100, 100), None, None),
    ("voltage",  "Tension batterie", "V",      "CONTROL_MODULE_VOLTAGE",    None,              (10, 16),    15.2, None),
    ("oil_life", "Vie de l'huile",   "%",      None,                        None,              (0, 100),    None, None),
]

# Valeur calculée : suralimentation = MAP - pression atmosphérique (en bar). Moteur essence à papillon :
# dépression à l'admission au ralenti et au lever de pied (≈ -0,7 bar), pression sous charge.
DERIVED = {"key": "boost", "label": "Suralimentation", "unit": "bar", "range": (-0.9, 1.6)}

# Réglages modifiables à chaud depuis l'onglet Paramètres : clé -> (min, max)
SETTINGS_LIMITS = {
    "max_hz": (0.5, 50.0),   # fréquence max de la boucle d'acquisition
    "delay": (0.0, 1.0),     # pause en s entre deux requêtes OBD
    "log_hz": (0.2, 50.0),   # fréquence du CSV
}
# Mode rapide python-obd : pas d'écho, nombre de réponses attendu → requêtes plus courtes.
# Certains clones d'ELM327 le gèrent mal. Changer ce réglage relance la connexion.
# Mode démo : valeurs simulées (demo.py) à la place de l'ECU, enregistrées dans une base à part (fiesta-demo.db).
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
        self.sources = {}     # clé → requête qui donne la mesure : {"kind": "obd" | "ford", "cmd": "01 5C"}
        self.calib = None     # etalonnage.Store : gain et décalage des mesures Ford
        self.raw_keys = []    # mesures Ford qui répondent : valeur brute dans « brut.<clé> » (onglet Étalonnage)
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
        """Valeur brute d'une mesure Ford : enregistrée telle quelle, et étalonnée si la mesure est validée."""
        self.update("brut." + key, raw)
        if self.calib is None:
            return
        if self.calib.get(key)[2]:
            self.update(key, None if raw is None else self.calib.apply(key, raw))

    def calib_changed(self):
        with self.lock:
            self.meta_rev += 1

    def shown(self, key):
        """Tuile affichée : mesure lue, et validée si c'est une mesure Ford."""
        if key not in self.supported:
            return False
        src = self.sources.get(key) or {}
        return src.get("kind") != "ford" or self.calib is None or self.calib.get(key)[2]

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
                # Mesures Ford qui répondent mais pas encore validées (onglet Étalonnage)
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
