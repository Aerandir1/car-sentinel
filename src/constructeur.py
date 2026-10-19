"""Mesures constructeur (Ford, Renault), lues sur le calculateur moteur quand l'OBD standard ne les donne pas.

Requêtes UDS « ReadDataByIdentifier » (service 0x22) : lecture seule, rien n'est jamais écrit dans le calculateur.
Les identifiants de chaque voiture sont dans son module (fiesta.FALLBACK, clio.FALLBACK). Tous les calculateurs ne
les connaissent pas : chacun est essayé à la connexion et gardé seulement s'il répond (voir probe). La valeur brute
est enregistrée telle quelle (clé « brut.<mesure> ») : un étalonnage corrigé plus tard s'applique aussi aux trajets
passés.

FALLBACK d'une voiture : clé de ses METRICS → (identifiant, gain, décalage, plage plausible, période de lecture en s,
octets, signé). valeur = brut * gain + décalage ; gain et décalage par défaut, que l'onglet Étalonnage du dashboard
remplace (data/etalonnage-<voiture>.json, voir etalonnage.py). La période allège la boucle : une température
d'huile n'a pas besoin d'être lue 10 fois par seconde.
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


def command(obd, car, key):
    """OBDCommand de la mesure constructeur qui remplace la mesure standard `key` (valeur brute), ou None."""
    if key not in car.FALLBACK:
        return None
    did, *_, nbytes, signed = car.FALLBACK[key]
    return obd.OBDCommand(f"{car.MAKER.upper()}_{did}", f"{car.MAKER} {key} (22 {did})", ("22" + did).encode(), 0,
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
