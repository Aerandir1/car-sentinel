"""Mesures propres à Renault, lues sur le calculateur moteur quand l'OBD standard ne les donne pas.

Requêtes UDS « ReadDataByIdentifier » (service 0x22) : lecture seule, rien n'est jamais écrit dans le calculateur.
Identifiants repris de la base ouverte OBDb (github.com/OBDb/Renault-Clio, CC BY-SA 4.0), relevés sur une Clio IV.
Tous les calculateurs ne les connaissent pas : chacun est essayé à la connexion et gardé seulement s'il répond
(voir probe). La valeur brute est enregistrée telle quelle (clé « brut.<mesure> ») : un étalonnage corrigé plus
tard s'applique aussi aux trajets passés.
"""

import time

ENGINE = b"7E0"   # calculateur moteur (réponses en 7E8)


def _did(did):
    """Décodeur d'une réponse 62 <DID> <2 octets> : valeur brute (entier non signé), None si refusé."""
    want = bytes.fromhex(did)

    def decode(messages):
        d = bytes(messages[0].data)
        if len(d) < 5 or d[0] != 0x62 or d[1:3] != want:   # 7F 22 xx : refus du calculateur
            return None
        return int.from_bytes(d[3:5], "big")
    return decode


# clé de METRICS (etat.py) → (identifiant, gain, décalage, plage plausible, période de lecture en s)
# valeur = brut * gain + décalage. Gain et décalage sont ceux par défaut : l'onglet Étalonnage du dashboard
# les remplace (data/etalonnage.json, voir etalonnage.py). La période allège la boucle : une température
# d'huile n'a pas besoin d'être lue 10 fois par seconde.
FALLBACK = {
    "oil":     ("2007", 0.1, -273.0, (-40, 160), 2.0),    # température d'huile (estimée par le calculateur)
    "voltage": ("2005", 0.01, 0.0, (6, 18), 1.0),         # tension d'alimentation du calculateur ≈ batterie
    # position de la pédale d'accélérateur, en 0,1 % : brut 1000 = pédale à fond (plafond relevé sur la voiture
    # à charge 100 %). OBDb donne 1/800, faux sur cette Clio : le max affiché n'était que 1,25 %.
    "pedal":   ("202E", 0.1, 0.0, (0, 100), 0.0),
    # Candidats trouvés par uds_scan.py (clio-iv-obd-utils), à étalonner avant d'être affichés :
    "baro":    ("2009", 0.1, 0.0, (70, 110), 5.0),        # 1020 relevé à l'arrêt : des hPa, donc ÷ 10 → kPa
    "egr":     ("2408", 0.01, -327.68, (0, 100), 0.5),    # consigne EGR, échelle OBDb (relevé à -4 % : douteux)
    "fuel":    ("2806", 0.01, 0.0, (0, 30), 0.5),         # débit carburant, unité inconnue (3,9 → 11,4 brut/100)
}
# Déjà vérifiées sur la voiture : affichées sans étalonnage. Les autres attendent sa validation.
TRUSTED = {"oil", "voltage", "pedal"}


def command(obd, key):
    """OBDCommand de la mesure Renault qui remplace la mesure standard `key` (valeur brute), ou None."""
    if key not in FALLBACK:
        return None
    did = FALLBACK[key][0]
    return obd.OBDCommand(f"RENAULT_{did}", f"Renault {key} (22 {did})", ("22" + did).encode(), 0,
                          _did(did), obd.ECU.ALL, False, ENGINE)


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
