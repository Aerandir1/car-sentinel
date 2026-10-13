"""Mesures propres à Renault, lues sur le calculateur moteur quand l'OBD standard ne les donne pas.

Requêtes UDS « ReadDataByIdentifier » (service 0x22) : lecture seule, rien n'est jamais écrit dans le calculateur.
Identifiants repris de la base ouverte OBDb (github.com/OBDb/Renault-Clio, CC BY-SA 4.0), relevés sur une Clio IV.
Tous les calculateurs ne les connaissent pas : chacun est essayé à la connexion et gardé seulement s'il répond
avec une valeur plausible (voir probe).
"""

import time

ENGINE = b"7E0"   # calculateur moteur (réponses en 7E8)


def _did(did, scale, offset, lo, hi):
    """Décodeur d'une réponse 62 <DID> <2 octets> : valeur * scale + offset, None hors de [lo, hi]."""
    want = bytes.fromhex(did)

    def decode(messages):
        d = bytes(messages[0].data)
        if len(d) < 5 or d[0] != 0x62 or d[1:3] != want:   # 7F 22 xx : refus du calculateur
            return None
        v = int.from_bytes(d[3:5], "big") * scale + offset
        return round(v, 2) if lo <= v <= hi else None
    return decode


# clé de METRICS (etat.py) → (identifiant, échelle, décalage, plage plausible, période de lecture en s)
# La période allège la boucle : une température d'huile n'a pas besoin d'être lue 10 fois par seconde.
FALLBACK = {
    "oil":     ("2007", 0.1, -273.0, (-40, 160), 2.0),    # température d'huile (estimée par le calculateur)
    "voltage": ("2005", 0.01, 0.0, (6, 18), 1.0),         # tension d'alimentation du calculateur ≈ batterie
    "pedal":   ("202E", 1 / 800, 0.0, (0, 100), 0.0),     # position de la pédale d'accélérateur
}


def command(obd, key):
    """OBDCommand de la mesure Renault qui remplace la mesure standard `key`, ou None."""
    if key not in FALLBACK:
        return None
    did, scale, offset, (lo, hi), _ = FALLBACK[key]
    return obd.OBDCommand(f"RENAULT_{did}", f"Renault {key} (22 {did})", ("22" + did).encode(), 0,
                          _did(did, scale, offset, lo, hi), obd.ECU.ALL, False, ENGINE)


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
