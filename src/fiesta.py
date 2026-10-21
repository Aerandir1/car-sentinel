"""Ford Fiesta VII (2022) 1.0 EcoBoost 95 FlexiFuel ST-Line : 3 cylindres essence, turbo, injection directe,
bicarburation essence / bioéthanol E85, boîte manuelle 6 vitesses.

Tout ce qui est propre à cette voiture : mesures, mesures Ford (UDS), seuils d'éco-conduite, simulation de la démo,
jauges du dashboard. Le reste du code est commun aux deux voitures (voir voitures.py).

Mesures Ford : identifiants et échelles de la base ouverte OBDb (github.com/OBDb/Ford-Fiesta, CC BY-SA 4.0).
D'après le relevé OBDb d'une Fiesta 2022, le PCM annonce en OBD standard le régime, la vitesse, les températures
eau / admission / huile / extérieure, la pression d'admission et atmo, la charge, le papillon, la pédale, la conso,
la richesse, les corrections de carburant, l'avance, le cat et la tension. Il n'annonce pas le débit d'air, la
pression rail ni le taux d'éthanol : seules les mesures Ford ci-dessous complètent.

Pas interrogés (relevés sur la voiture) : le débit d'air (01 10) ne répond jamais, la température d'air de
suralimentation (Ford 0461) vaut toujours -40 °C (brut 0). Ils prenaient une place dans chaque tour de lecture.
"""

ID = "fiesta"
NAME = "Ford Fiesta 1.0 EcoBoost"
TITLE = "Fiesta 1.0 EcoBoost"            # en-tête du dashboard
SUBTITLE = "1.0 EcoBoost 95 · FlexiFuel"  # écran de démarrage
MAKER = "Ford"
WMI = ("WF0",)                 # début du VIN : Ford Allemagne (Cologne)
FUEL_TYPES = ("ethanol", "bifuel", "gasoline")   # 01 51 (minuscules) : FlexiFuel essence / E85
FUEL = "E85 ou SP95"           # carburant du plein (prix au litre)

# Pédale d'accélérateur (01 49, capteur D) : le calculateur la donne en % de la tension, 15,69 % au repos et 80 % au
# plancher (lu par pas de 0,39 %). Ramenée à la course de la pédale : 0 au repos, 100 au plancher.
# Sous régulateur, elle reste au repos : c'est le calculateur qui ouvre le papillon.
PEDAL_REST, PEDAL_FULL = 15.69, 80.0


def pedal_travel(v):
    return round(min(100.0, max(0.0, (v - PEDAL_REST) * 100 / (PEDAL_FULL - PEDAL_REST))), 1)


# key, libellé, unité, commande python-obd (None : mesure Ford seulement), conversion,
# min/max d'affichage, seuil d'alerte (rouge au-delà), seuil "froid" (bleu en dessous)
METRICS = [
    ("rpm",      "Régime",           "tr/min", "RPM",                       None,              (0, 7000),   6300, None),
    ("speed",    "Vitesse",          "km/h",   "SPEED",                     None,              (0, 200),    None, None),
    ("gear",     "Rapport",          "",       None,                        None,              (0, 6),      None, None),
    ("coolant",  "Temp. eau",        "°C",     "COOLANT_TEMP",              None,              (-20, 130),  110,  70),
    ("cht",      "Temp. culasse",    "°C",     None,                        None,              (-20, 160),  125,  70),
    ("oil",      "Temp. huile",      "°C",     "OIL_TEMP",                  None,              (-20, 150),  130,  70),
    ("intake",   "Temp. admission",  "°C",     "INTAKE_TEMP",               None,              (-20, 90),   60,   None),
    ("cat",      "Temp. catalyseur", "°C",     "CATALYST_TEMP_B1S1",        None,              (0, 1000),   900,  300),
    ("ambient",  "Temp. extérieure", "°C",     "AMBIANT_AIR_TEMP",          None,              (-30, 50),   None, None),
    ("map",      "Pression admission", "kPa",  "INTAKE_PRESSURE",           None,              (0, 250),    None, None),
    ("tip",      "Pression turbo",   "kPa",    None,                        None,              (50, 250),   None, None),
    ("baro",     "Pression atmo",    "kPa",    "BAROMETRIC_PRESSURE",       None,              (70, 110),   None, None),
    ("wgdc",     "Wastegate",        "%",      None,                        None,              (0, 100),    None, None),
    ("load",     "Charge moteur",    "%",      "ENGINE_LOAD",               None,              (0, 100),    None, None),
    ("pedal",    "Pédale accél.",    "%",      "ACCELERATOR_POS_D",         pedal_travel,      (0, 100),    None, None),
    ("throttle", "Papillon",         "%",      "THROTTLE_POS",              None,              (0, 100),    None, None),
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

# Valeurs calculées (voir etat.State.derive) :
#   suralimentation = MAP - pression atmosphérique (en bar). Moteur essence à papillon : dépression à l'admission
#                     au ralenti et au lever de pied (≈ -0,7 bar), pression sous charge.
#   conso instantanée = débit de carburant / vitesse × 100 (L/100 km), comme l'ordinateur de bord ; rien à l'arrêt.
# needs : mesures dont elle dépend (la tuile n'apparaît que si le calculateur les donne)
DERIVED = [
    {"key": "boost", "label": "Suralimentation", "unit": "bar", "range": (-0.9, 1.6), "needs": ("map",)},
    {"key": "l100", "label": "Conso. instantanée", "unit": "L/100 km", "range": (0, 30), "needs": ("fuel", "speed")},
]

# Mesures Ford (voir constructeur.py) : (identifiant, gain, décalage, plage plausible, période s, octets, signé)
FALLBACK = {
    # Équivalents de mesures OBD standard, si le calculateur ne les annonce pas
    "oil":      ("1310", 0.01, -40.0, (-40, 160), 2.0, 2, False),      # température d'huile
    "voltage":  ("1172", 0.0625, 0.0, (6, 18), 1.0, 1, False),         # tension batterie
    "pedal":    ("09D4", 0.5, 0.0, (0, 100), 0.0, 1, False),           # pédale d'accélérateur
    "fuel_lvl": ("F42F", 100 / 255, 0.0, (0, 100), 10.0, 1, False),    # niveau de carburant
    # Mesures Ford sans équivalent standard
    "cht":      ("0334", 1 / 64, 0.0, (-40, 200), 1.0, 2, True),       # température de culasse
    # pression avant le papillon = sortie du turbo (OBDb : psi = brut × 37 / 33441 ; ici en kPa)
    "tip":      ("033E", 37 / 33441 * 6.894757, 0.0, (0, 300), 0.0, 2, False),
    "wgdc":     ("0462", 100 / 32768, 0.0, (0, 100), 0.5, 2, False),   # ouverture commandée de la wastegate
    "rail_des": ("03DC", 0.1, 0.0, (0, 300), 0.5, 2, False),           # consigne de pression rail (kPa × 10 → bar)
    "ign_corr": ("03EC", 1 / 512, 0.0, (-30, 30), 0.5, 2, True),       # correction d'allumage cylindre 1 (cliquetis)
    "octane":   ("03E8", 100 / 16384, 0.0, (-100, 100), 2.0, 2, True), # adaptation octane, en %
    "gear":     ("1E12", 1.0, 0.0, (0, 6), 0.5, 1, False),             # rapport engagé (calculé par le PCM)
    "oil_life": ("054B", 1.0, 0.0, (0, 100), 10.0, 1, False),          # durée de vie d'huile restante
}
# Mesures affichées sans étalonnage : équivalents directs de l'OBD standard. Les autres attendent sa validation
# sur la voiture.
TRUSTED = {"oil", "voltage", "pedal", "fuel_lvl"}
# PID standard essayés même si le calculateur ne les annonce pas (PID 00, 20, 40…) : le taux d'éthanol (01 52)
# d'une FlexiFuel peut répondre sans être annoncé. Gardé seulement s'il répond.
FORCE_PROBE = {"ethanol"}
# Limitation de vitesse lue par la caméra (reconnaissance des panneaux) : message CAN diffusé par l'IPMA vers le
# combiné, écouté entre deux tours de lecture (panneaux.py)
SIGN_CAN = 0x3CD

# Éco-conduite (trajets.py). Rapport engagé déduit de régime / vitesse (tr/min par km/h, mesuré sur les trajets) :
# médianes de 345 km enregistrés : 1re 125,5, 2e 70,3, 3e 45,3, 4e 33,4, 5e 26,7, 6e 22,3 (100 km/h ≈ 2230 tr/min
# en 6e ; la 1re ne se voit qu'en dessous de 20 km/h)
GEARS = [125.5, 70.3, 45.3, 33.4, 26.7, 22.3]
TOP_GEAR_RATIO = 24       # au-dessus : 5e ou moins
SHIFT_RPM = 2500          # au-delà, sur un rapport intermédiaire : il fallait monter (1.0 EcoBoost : couple dès 1500)
PEDAL_HEAVY = 70          # % de la course de la pédale au-delà : pied lourd (jamais sous régulateur, pédale au repos)
SHIFT_TIP = "passer la vitesse supérieure vers 2000-2500 tr/min, le 1.0 EcoBoost a son couple dès 1500"

# Jauges du dashboard : compte-tours et suralimentation (dépression au ralenti, essence à papillon)
GAUGES = {
    "tach": {"max": 7000, "redline": 6300, "majors": [0, 1000, 2000, 3000, 4000, 5000, 6000, 7000]},
    "boost": {"min": -0.8, "max": 1.6, "redline": 1.3, "majors": [-0.8, -0.4, 0, 0.4, 0.8, 1.2, 1.6]},
}

# --- Démo (demo.py) ---
DEMO_VIN = "WF0DEMO0000000000"   # faux numéro de série, reconnaissable (WF0 : Ford Allemagne)
IDLE_RPM = 900
ETHANOL = 72.0   # E85 en hiver (≈ 70 % d'éthanol à la pompe), lu par le PCM sur le capteur de carburant
# Valeurs brutes des mesures Ford à étalonner (onglet Étalonnage) : brut = (valeur - décalage) / gain,
# avec une échelle volontairement différente de celle par défaut (FALLBACK) pour avoir à la corriger
DEMO_RAW = {"tip": (0.0081, 0.0), "octane": (0.0049, 0.0), "wgdc": (0.0025, 0.0)}


def demo_values(s, noise):
    """Mesures simulées d'un instant. s : régime, vitesse, rapport, pédale, moteur coupé, pas de temps (demo.py),
    et ce que la simulation garde d'un pas à l'autre (températures)."""
    rpm, speed, pedal, off = s["rpm"], s["speed"], s["pedal"], s["engine_off"]
    load = 0.0 if off else noise(15 + pedal * 0.85, 1.5) if pedal else noise(6 if speed > 4 else 16, 1)
    # Essence à papillon : dépression au ralenti et pied levé, le turbo souffle sous charge à partir de ~1500 tr/min
    spool = max(0.0, min(1.0, (rpm - 1500) / 1000))
    boost = -0.72 + load / 100 * 0.75 + spool * max(0.0, load - 45) / 55 * 1.05
    boost = max(-0.75, min(1.1, noise(boost, 0.01)))
    baro, ambient = s["baro"], s["ambient"]
    tip = baro + max(boost, 0.0) * 100 + 3 + spool * load * 0.2   # avant le papillon : jamais en dépression
    s["coolant"] += (0.3 if s["coolant"] < 92 else 0.0) * s["dt"] * (1 + load / 50)
    s["coolant"] = min(s["coolant"], noise(92.5, 0.3))
    s["oil"] += (s["coolant"] + 4 - s["oil"]) * 0.004
    fuel = 0.0 if off else (0.45 + load / 100 * rpm / 1000 * 2.4) * (1 + ETHANOL / 100 * 0.35)
    return {
        "rpm": rpm, "speed": speed, "gear": 0 if off or speed < 4 else s["gear"] + 1,
        "coolant": s["coolant"], "cht": s["coolant"] + 6 + load * 0.15, "oil": s["oil"],
        "intake": ambient + 6,
        "cat": 0.0 if off else 420 + load * 4.5 + rpm * 0.04,
        "ambient": ambient,
        "map": baro + boost * 100, "tip": tip, "baro": baro,
        "wgdc": 0.0 if off else max(0.0, min(100.0, 85 - spool * load * 0.9)),
        "load": load, "pedal": pedal,
        "throttle": 0.0 if off else 4.0 + pedal * 0.8 if pedal else 3.5,
        "ethanol": ETHANOL,
        "fuel": fuel,
        "fuel_lvl": 63.0,
        "rail": 0.0 if off else 40 + load * 1.4,
        "rail_des": 0.0 if off else 40 + load * 1.4,
        "lambda": 1.0 if pedal < 85 else 0.88,
        "stft": noise(0, 3), "ltft": noise(2.3, 0.2),
        "timing": 0.0 if off else 12 - load * 0.08 + (rpm - 900) / 400,
        "ign_corr": 0.0 if load < 70 else noise(-1.2, 0.6),
        "octane": 100.0 * min(1.0, ETHANOL / 70),
        "voltage": noise(14.3, 0.04),
        "oil_life": 78.0,
    }
