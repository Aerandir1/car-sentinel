"""Positions GPS de la tablette : SentinelService.java (LocationManager d'Android) appelle fix() à chaque position,
qui la passe à l'état du dashboard (etat.State.gps_fix). Sur PC, rien ne l'appelle : pas de GPS."""

import math

STATE = None   # etat.State du dashboard, réglé par car_sentinel.main


def fix(lat, lon, speed_ms, alt, head, acc, t_ms):
    """Une position d'Android : vitesse en m/s, cap en °, précision en m ; NaN quand Android ne la donne pas."""
    if STATE is None:
        return
    val = lambda v: None if v is None or math.isnan(v) else float(v)
    speed = val(speed_ms)
    STATE.gps_fix(float(lat), float(lon), None if speed is None else speed * 3.6, val(alt), val(head), val(acc),
                  int(t_ms) or None)
