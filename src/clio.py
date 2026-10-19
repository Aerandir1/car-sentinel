"""Renault Clio IV dCi 90 (moteur K9K) : 4 cylindres diesel, turbo, injection directe à rampe commune, EGR,
boîte manuelle 5 vitesses.

Tout ce qui est propre à cette voiture : mesures, mesures Renault (UDS), seuils d'éco-conduite, simulation de la
démo, jauges du dashboard. Le reste du code est commun aux deux voitures (voir voitures.py).

Mesures Renault : identifiants repris de la base ouverte OBDb (github.com/OBDb/Renault-Clio, CC BY-SA 4.0), relevés
sur une Clio IV, et candidats trouvés par uds_scan.py (clio-iv-obd-utils).
"""

ID = "clio"
NAME = "Renault Clio IV dCi 90"
TITLE = "Clio IV dCi 90"       # en-tête du dashboard
SUBTITLE = "Clio 4 dCi 90"     # écran de démarrage
MAKER = "Renault"
WMI = ("VF1",)                 # début du VIN : Renault (France)
FUEL_TYPES = ("diesel",)       # 01 51 (minuscules)
FUEL = "gazole"                # carburant du plein (prix au litre)

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

# Valeurs calculées (voir etat.State.derive) :
#   suralimentation = MAP - pression atmosphérique (en bar)
#   conso instantanée = débit de carburant / vitesse × 100 (L/100 km), comme l'ordinateur de bord ; rien à l'arrêt.
# needs : mesures dont elle dépend (la tuile n'apparaît que si le calculateur les donne)
DERIVED = [
    {"key": "boost", "label": "Suralimentation", "unit": "bar", "range": (-0.2, 1.6), "needs": ("map",)},
    {"key": "l100", "label": "Conso. instantanée", "unit": "L/100 km", "range": (0, 20), "needs": ("fuel", "speed")},
]

# Mesures Renault (voir constructeur.py) : (identifiant, gain, décalage, plage plausible, période s, octets, signé)
FALLBACK = {
    "oil":     ("2007", 0.1, -273.0, (-40, 160), 2.0, 2, False),    # température d'huile (estimée par le calculateur)
    "voltage": ("2005", 0.01, 0.0, (6, 18), 1.0, 2, False),         # tension d'alimentation du calculateur ≈ batterie
    # position de la pédale d'accélérateur, en 0,1 % : brut 1000 = pédale à fond (plafond relevé sur la voiture
    # à charge 100 %). OBDb donne 1/800, faux sur cette Clio : le max affiché n'était que 1,25 %.
    "pedal":   ("202E", 0.1, 0.0, (0, 100), 0.0, 2, False),
    # Candidats trouvés par uds_scan.py (clio-iv-obd-utils), à étalonner avant d'être affichés :
    "baro":    ("2009", 0.1, 0.0, (70, 110), 5.0, 2, False),        # 1020 relevé à l'arrêt : des hPa, donc ÷ 10 → kPa
    "egr":     ("2408", 0.01, -327.68, (0, 100), 0.5, 2, False),    # consigne EGR, échelle OBDb (relevé à -4 % : douteux)
    "fuel":    ("2806", 0.01, 0.0, (0, 30), 0.5, 2, False),         # débit carburant, unité inconnue (3,9 → 11,4 brut/100)
}
# Déjà vérifiées sur la voiture : affichées sans étalonnage. Les autres attendent sa validation.
TRUSTED = {"oil", "voltage", "pedal"}
FORCE_PROBE = set()

# Éco-conduite (trajets.py). Rapport engagé déduit de régime / vitesse (tr/min par km/h, boîte 5 vitesses du K9K :
# 100 km/h ≈ 2300 tr/min en 5e) : 1re ≈ 120, 2e ≈ 65, 3e ≈ 42, 4e ≈ 30, 5e ≈ 23. Estimations, à vérifier sur les
# trajets enregistrés comme pour la Fiesta.
GEARS = [120, 65, 42, 30, 23]
TOP_GEAR_RATIO = 26       # au-dessus : 4e ou moins
SHIFT_RPM = 2000          # au-delà, sur un rapport intermédiaire : il fallait monter (diesel : couple dès 1750)
PEDAL_HEAVY = 70          # % de la pédale au-delà : pied lourd
SHIFT_TIP = "passer la vitesse supérieure vers 1800-2000 tr/min, le dCi a son couple dès 1750"

# Jauges du dashboard : compte-tours (diesel, rupteur bas) et suralimentation (jamais en dépression)
GAUGES = {
    "tach": {"max": 5000, "redline": 4500, "majors": [0, 1000, 2000, 3000, 4000, 5000]},
    "boost": {"min": -0.2, "max": 1.6, "redline": 1.35, "majors": [0, 0.4, 0.8, 1.2, 1.6]},
}

# --- Démo (demo.py) ---
DEMO_VIN = "VF1DEMO0000000000"   # faux numéro de série, reconnaissable (VF1 : Renault)
IDLE_RPM = 820
# Valeurs brutes des mesures Renault à étalonner (onglet Étalonnage) : brut = (valeur - décalage) / gain,
# avec une échelle volontairement différente de celle par défaut (FALLBACK) pour avoir à la corriger
DEMO_RAW = {"fuel": (0.0042, 0.0), "egr": (0.0125, -409.6), "baro": (0.1, 0.0)}


def demo_values(s, noise):
    """Mesures simulées d'un instant. s : régime, vitesse, rapport, pédale, moteur coupé, pas de temps (demo.py),
    et ce que la simulation garde d'un pas à l'autre (températures)."""
    rpm, speed, pedal, off = s["rpm"], s["speed"], s["pedal"], s["engine_off"]
    load = 0.0 if off else noise(18 + pedal * 0.8, 1.5) if pedal else noise(8 if speed > 4 else 18, 1)
    boost = max(-0.02, min(1.35, (rpm - 1400) / 1300 * load / 100 * 1.6))
    baro, ambient = s["baro"], s["ambient"]
    s["coolant"] += (0.25 if s["coolant"] < 89 else 0.0) * s["dt"] * (1 + load / 50)
    s["coolant"] = min(s["coolant"], noise(90.5, 0.3))
    s["oil"] += (s["coolant"] + 6 - s["oil"]) * 0.004
    return {
        "rpm": rpm, "speed": speed,
        "coolant": s["coolant"], "oil": s["oil"],
        "intake": ambient + 8 + boost * 18,
        "map": baro + boost * 100, "baro": baro,
        "load": load, "pedal": pedal,
        "throttle": 100.0 if pedal else 88.0,
        "maf": rpm / 1000 * (7 + boost * 14),
        "rail": 0.0 if off else 280 + load * 11 + rpm * 0.12,
        "fuel": 0.0 if off else 0.55 + load / 100 * rpm / 1000 * 3.2,
        "egr": 0.0 if off else 35.0 if load < 45 and rpm < 2600 else 4.0,
        "egr_err": noise(0, 2.5),
        "ambient": ambient,
        "voltage": noise(14.15, 0.04),
    }
