"""Mesures propres à Ford, lues sur le calculateur moteur (PCM) quand l'OBD standard ne les donne pas.

Requêtes UDS « ReadDataByIdentifier » (service 0x22) : lecture seule, rien n'est jamais écrit dans le calculateur.
Identifiants et échelles repris de la base ouverte OBDb (github.com/OBDb/Ford-Fiesta, CC BY-SA 4.0).
Tous les calculateurs ne les connaissent pas : chacun est essayé à la connexion et gardé seulement s'il répond
(voir probe). La valeur brute est enregistrée telle quelle (clé « brut.<mesure> ») : un étalonnage corrigé plus
tard s'applique aussi aux trajets passés.

D'après le relevé OBDb d'une Fiesta 2022, le PCM annonce en OBD standard le régime, la vitesse, les températures
eau / admission / huile / extérieure, la pression d'admission et atmo, la charge, le papillon, la pédale, la conso,
la richesse, les corrections de carburant, l'avance, le cat et la tension : ces mesures-là passent par l'OBD
standard. Il n'annonce pas le débit d'air, la pression rail ni le taux d'éthanol : seules les mesures Ford
ci-dessous complètent.
"""

import time

ENGINE = b"7E0"   # calculateur moteur (réponses en 7E8)


def _did(did, nbytes, signed):
    """Décodeur d'une réponse 62 <DID> <nbytes octets> : valeur brute (entier), None si refusé."""
    want = bytes.fromhex(did)

    def decode(messages):
        d = bytes(messages[0].data)
        if len(d) < 3 + nbytes or d[0] != 0x62 or d[1:3] != want:   # 7F 22 xx : refus du calculateur
            return None
        return int.from_bytes(d[3:3 + nbytes], "big", signed=signed)
    return decode


# clé de METRICS (etat.py) → (identifiant, gain, décalage, plage plausible, période de lecture en s, octets, signé)
# valeur = brut * gain + décalage. Gain et décalage sont ceux d'OBDb : l'onglet Étalonnage du dashboard les
# remplace (data/etalonnage.json, voir etalonnage.py). La période allège la boucle : une température d'huile n'a
# pas besoin d'être lue 10 fois par seconde.
FALLBACK = {
    # Équivalents de mesures OBD standard, si le calculateur ne les annonce pas
    "oil":      ("1310", 0.01, -40.0, (-40, 160), 2.0, 2, False),      # température d'huile
    "voltage":  ("1172", 0.0625, 0.0, (6, 18), 1.0, 1, False),         # tension batterie
    "pedal":    ("09D4", 0.5, 0.0, (0, 100), 0.0, 1, False),           # pédale d'accélérateur
    "fuel_lvl": ("F42F", 100 / 255, 0.0, (0, 100), 10.0, 1, False),    # niveau de carburant
    # Mesures Ford sans équivalent standard
    "cac":      ("0461", 1 / 64, -40.0, (-40, 150), 1.0, 2, True),     # air après l'échangeur (relevé OBDb 2017-2021)
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
# Mesures affichées sans étalonnage : équivalents directs de l'OBD standard, et la température d'air de
# suralimentation, vérifiée sur plusieurs Fiesta dans OBDb. Les autres attendent sa validation sur la voiture.
TRUSTED = {"oil", "voltage", "pedal", "fuel_lvl", "cac"}


def command(obd, key):
    """OBDCommand de la mesure Ford qui remplace la mesure standard `key` (valeur brute), ou None."""
    if key not in FALLBACK:
        return None
    did, *_, nbytes, signed = FALLBACK[key]
    return obd.OBDCommand(f"FORD_{did}", f"Ford {key} (22 {did})", ("22" + did).encode(), 0,
                          _did(did, nbytes, signed), obd.ECU.ALL, False, ENGINE)


def probe(conn, cmd):
    """Le calculateur connaît-il cette mesure ? Deux essais (la première requête réveille parfois la session)."""
    for _ in range(2):
        r = conn.query(cmd, force=True)
        if not r.is_null() and r.value is not None:
            return True
    return False


class Slow:
    """Lecture espacée : due() vrai au plus une fois par période."""

    def __init__(self, period):
        self.period, self.last = period, 0.0

    def due(self):
        if time.time() - self.last < self.period:
            return False
        self.last = time.time()
        return True
