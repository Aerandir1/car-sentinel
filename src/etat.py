"""Métriques lues sur l'ECU et état partagé entre le thread d'acquisition et le serveur HTTP.

Pour alléger ou compléter les mesures, éditer la liste METRICS.
"""

import csv
import json
import threading
import time
from collections import deque

import donnees

BARO_DEFAULT_KPA = 101.3
# Pédale d'accélérateur (01 49, capteur D) : le calculateur la donne en % de la tension, 15,69 % au repos et 80 % au
# plancher sur la Fiesta (lu par pas de 0,39 %). Ramenée à la course de la pédale : 0 au repos, 100 au plancher.
# Sous régulateur, elle reste au repos : c'est le calculateur qui ouvre le papillon.
PEDAL_REST, PEDAL_FULL = 15.69, 80.0


def pedal_travel(v):
    return round(min(100.0, max(0.0, (v - PEDAL_REST) * 100 / (PEDAL_FULL - PEDAL_REST))), 1)


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
    ("pedal",    "Pédale accél.",    "%",      "ACCELERATOR_POS_D",         pedal_travel,      (0, 100),    None, None),
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

# Valeurs calculées, enregistrées comme les autres :
#   suralimentation = MAP - pression atmosphérique (en bar). Moteur essence à papillon : dépression à l'admission
#                     au ralenti et au lever de pied (≈ -0,7 bar), pression sous charge.
#   conso instantanée = débit de carburant / vitesse × 100 (L/100 km), comme l'ordinateur de bord ; rien à l'arrêt.
# needs : mesures dont elle dépend (la tuile n'apparaît que si le calculateur les donne)
DERIVED = [
    {"key": "boost", "label": "Suralimentation", "unit": "bar", "range": (-0.9, 1.6), "needs": ("map",)},
    {"key": "l100", "label": "Conso. instantanée", "unit": "L/100 km", "range": (0, 30), "needs": ("fuel", "speed")},
]
L100_MIN_KMH = 5     # en dessous (arrêt, manœuvre), pas de L/100 km
L100_MAX = 99.0      # pied au plancher en 1re : plafonné comme sur l'ordinateur de bord
TRIP_GAP_MS = 10_000  # plus longtemps entre deux mesures : le trou ne compte pas (comme trajets.RUN_GAP_S)

# Réglages modifiables à chaud depuis l'onglet Paramètres : clé -> (min, max)
SETTINGS_LIMITS = {
    "max_hz": (0.5, 50.0),   # fréquence max de la boucle d'acquisition
    "delay": (0.0, 1.0),     # pause en s entre deux requêtes OBD
    "log_hz": (0.2, 50.0),   # fréquence du CSV
    "fuel_price": (0.0, 10.0),   # prix du carburant en €/L (coût des trajets), gardé dans data/reglages.json
}
SETTINGS_SAVED = ("fuel_price",)   # réglages gardés d'un lancement à l'autre (les autres viennent des options)
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
        self.fuel_type = None # type de carburant annoncé (01 51), ex. « Bifuel running Ethanol »
        self.trip = None      # identifiant du trajet en cours (mis à jour par trajets.py)
        # Carburant et distance du trajet en cours : la fiche calculée par trajets.py toutes les 10 s, plus ce qui
        # a été lu depuis (increments), pour un coût qui monte en direct
        self.trip_base = None     # {"id", "end_ms", "fuel_l", "distance_km"}
        self.increments = deque() # (t_ms, litres, km) depuis la dernière fiche
        self.integ = {}           # "fuel" / "speed" → (t_ms, valeur) de la dernière mesure intégrée
        self.settings_file = None # data/reglages.json (SETTINGS_SAVED)
        self.hz = 0.0
        self.sync = None      # dernier essai de synchro vers clio-sentinel-server (voir synchro.py)
        self.settings = {}

    def get_settings(self):
        with self.lock:
            return dict(self.settings)

    def set_settings(self, new):
        with self.lock:
            for key, (lo, hi) in SETTINGS_LIMITS.items():
                if key in new and new[key] is not None:
                    self.settings[key] = min(hi, max(lo, float(new[key])))
            for key in SETTINGS_FLAGS:
                if key in new:
                    self.settings[key] = bool(new[key])
            saved = {k: self.settings[k] for k in SETTINGS_SAVED if k in self.settings}
            out = dict(self.settings)
        if self.settings_file and any(k in new for k in SETTINGS_SAVED):
            tmp = self.settings_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.settings_file)
        return out

    def load_settings(self, path):
        """Réglages gardés (prix du carburant…) : lus au lancement, réécrits à chaque changement."""
        self.settings_file = path
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        with self.lock:
            for key in SETTINGS_SAVED:
                if isinstance(saved.get(key), (int, float)):
                    lo, hi = SETTINGS_LIMITS[key]
                    self.settings[key] = min(hi, max(lo, float(saved[key])))

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
            fuel, t_fuel = self.values.get("fuel"), self.times.get("fuel")
            speed = self.values.get("speed")
            new_fuel = t_fuel is not None and t_fuel != self.integ.get("fuel", (None,))[0]
            self._integrate()
        if m is not None:
            self.update("boost", round((m - baro) / 100, 3), t_map)   # même instant que la pression d'admission
        if new_fuel:   # une valeur par nouvelle mesure de conso, au même instant
            if fuel is not None and speed is not None and speed >= L100_MIN_KMH:
                self.update("l100", round(min(L100_MAX, 100 * fuel / speed), 2), t_fuel)
            else:   # à l'arrêt : rien d'enregistré (ce n'est pas une absence de réponse), tuile vide
                with self.lock:
                    self.values["l100"] = None

    def _integrate(self):
        """Carburant (L/h) et vitesse (km/h) intégrés depuis la dernière mesure, comme trajets.py (trapèzes,
        trous de plus de 10 s ignorés). Appelé sous self.lock."""
        for key in ("fuel", "speed"):
            t, v = self.times.get(key), self.values.get(key)
            if t is None or v is None:
                continue
            prev = self.integ.get(key)
            self.integ[key] = (t, v)
            if prev and prev[0] is not None and 0 < t - prev[0] <= TRIP_GAP_MS:
                amount = (prev[1] + v) / 2 * (t - prev[0]) / 3_600_000
                self.increments.append((t, amount, 0.0) if key == "fuel" else (t, 0.0, amount))
        while len(self.increments) > 20_000:   # pas de trajet (pas de base) pendant longtemps
            self.increments.popleft()

    def set_trip(self, row):
        """Fiche du trajet en cours (trajets.update), None sans trajet en cours."""
        with self.lock:
            self.trip = row["id"] if row else None
            self.trip_base = row and {k: row[k] for k in ("id", "end_ms", "fuel_l", "distance_km")}
            if row:
                while self.increments and self.increments[0][0] <= row["end_ms"]:
                    self.increments.popleft()

    def _trip_live(self):
        """Carburant et distance du trajet en cours, à jour de la dernière mesure. Appelé sous self.lock."""
        b = self.trip_base
        if not b:
            return None
        fuel = (b["fuel_l"] or 0.0) + sum(i[1] for i in self.increments if i[0] > b["end_ms"])
        km = (b["distance_km"] or 0.0) + sum(i[2] for i in self.increments if i[0] > b["end_ms"])
        has_fuel = b["fuel_l"] is not None or any(i[1] for i in self.increments)
        return {"id": b["id"], "fuel_l": round(fuel, 4) if has_fuel else None, "distance_km": round(km, 3)}

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
            for d in (self.values, self.times, self.mins, self.maxs, self.integ):
                d.clear()
            self.increments.clear()
            self.trip, self.trip_base = None, None   # l'autre base : sa fiche arrive au prochain passage de trajets.py
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
                "trip_live": self._trip_live(),
                "fuel_price": self.settings.get("fuel_price"),
                "meta_rev": self.meta_rev,
                "sync": self.sync,
                "link": {"elm": self.elm, "ecu": self.ecu, "name": self.elm_name, "port": self.port,
                         "protocol": self.protocol, "vin": self.vin,
                         "fuel_type": self.fuel_type},
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
            for d in DERIVED:
                if all(self.shown(k) for k in d["needs"]):
                    metrics.append({**derived_meta(d), "source": {"kind": "calc", "cmd": None}})
            return {
                "source": self.source,
                "vin": self.vin,
                "metrics": metrics,
                "unsupported": [lbl for k, lbl, *_ in METRICS if k in self.unsupported],
                # Mesures Ford qui répondent mais pas encore validées (onglet Étalonnage)
                "to_calibrate": [lbl for k, lbl, *_ in METRICS if k in self.raw_keys and k not in shown],
            }


def derived_meta(d):
    """Valeur calculée, au format des METRICS (sans seuils)."""
    return {k: d[k] for k in ("key", "label", "unit", "range")} | {"warn": None, "cold": None}


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
