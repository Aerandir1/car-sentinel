"""Les voitures connues et la reconnaissance de celle qui est branchée.

Chaque voiture a son module (clio.py, fiesta.py) : mesures, mesures constructeur, éco-conduite, démo, jauges.
À la connexion, le VIN dit laquelle c'est (ses trois premiers caractères : le constructeur, VF1 Renault, WF0 Ford) ;
sans VIN, le type de carburant annoncé par le calculateur (01 51 : diesel ou FlexiFuel). Sinon, la dernière voiture
vue (réglage « car », gardé dans data/reglages.json) : c'est aussi celle de la démo et du dashboard sans voiture.

Chaque voiture garde ses propres fichiers dans data/ : base des mesures (<id>.db, <id>-demo.db), étalonnage
(etalonnage-<id>.json), scan des PID (scan-pid-<id>.json). Le serveur reçoit chaque base comme une origine à part.
"""

import clio
import fiesta

CARS = {c.ID: c for c in (clio, fiesta)}
DEFAULT = fiesta.ID


def get(car_id):
    return CARS.get(car_id) or CARS[DEFAULT]


def detect(vin=None, fuel_type=None):
    """Identifiant de la voiture d'après son VIN, sinon son type de carburant ; None si rien ne la reconnaît."""
    vin = (vin or "").upper()
    for car in CARS.values():
        if vin.startswith(car.WMI):
            return car.ID
    fuel = (fuel_type or "").lower()
    found = [car.ID for car in CARS.values() if any(w in fuel for w in car.FUEL_TYPES)]
    return found[0] if len(found) == 1 else None


def labels():
    """Libellés de toutes les mesures connues, toutes voitures confondues (diagnostic)."""
    out = {}
    for car in CARS.values():
        out.update({k: lbl for k, lbl, *_ in car.METRICS})
    return out


def info(car):
    """Ce que le dashboard doit savoir de la voiture pour s'habiller (nom, logo, jauges, carburant)."""
    return {"id": car.ID, "name": car.NAME, "title": car.TITLE, "subtitle": car.SUBTITLE, "maker": car.MAKER,
            "fuel": car.FUEL, "gears": car.GEARS, "gauges": car.GAUGES, "shift_tip": car.SHIFT_TIP}


def choices():
    return [{"id": c.ID, "name": c.NAME} for c in CARS.values()]
