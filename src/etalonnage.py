"""Étalonnage des mesures Ford : gain et décalage réglés dans l'onglet Étalonnage du dashboard.

valeur = brut * gain + décalage. Sans réglage, ceux de ford.FALLBACK. Une mesure candidate n'est affichée en
tuile qu'une fois validée (les mesures de ford.TRUSTED le sont d'office).
Rangé dans data/etalonnage.json, avec les références notées (valeur lue sur l'ordinateur de bord) :

    {"fuel": {"gain": 0.0042, "offset": 0.0, "validated": true, "updated": "2026-10-13 15:02:11",
              "refs": [{"t0": …, "t1": …, "ref": 5.4, "unit": "L/100 km", "a": …, "b": …}]}}
"""

import json
import math
import os
import threading
import time

import ford

MAX_REFS = 50


class Store:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.data = {k: v for k, v in json.load(f).items() if k in ford.FALLBACK}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f"Attention : {path} illisible ({e}) : étalonnage par défaut")

    def get(self, key):
        """(gain, décalage, validé) de la mesure."""
        _did, gain, offset, *_ = ford.FALLBACK[key]
        with self.lock:
            c = self.data.get(key, {})
        return c.get("gain", gain), c.get("offset", offset), c.get("validated", key in ford.TRUSTED)

    def apply(self, key, raw):
        """Valeur étalonnée, None si hors de la plage plausible de la mesure."""
        gain, offset, _ = self.get(key)
        v = raw * gain + offset
        lo, hi = ford.FALLBACK[key][3]
        return round(v, 2) if lo <= v <= hi else None

    def describe(self):
        """Toutes les mesures Ford, pour l'onglet Étalonnage."""
        out = {}
        for key, (did, gain, offset, rng, *_) in ford.FALLBACK.items():
            g, o, ok = self.get(key)
            with self.lock:
                c = self.data.get(key, {})
            out[key] = {"did": did, "gain": g, "offset": o, "validated": ok, "range": rng,
                        "default_gain": gain, "default_offset": offset, "trusted": key in ford.TRUSTED,
                        "refs": c.get("refs", []), "updated": c.get("updated")}
        return out

    def save(self, key, change):
        """Enregistre gain, décalage, validation et références (champs absents : inchangés). ValueError si invalide."""
        if key not in ford.FALLBACK:
            raise ValueError("mesure inconnue")
        with self.lock:
            c = dict(self.data.get(key, {}))
            if change.get("reset"):
                c = {"refs": c.get("refs", [])}
            for k in ("gain", "offset"):
                if k in change:
                    v = float(change[k])
                    if not math.isfinite(v) or (k == "gain" and v == 0):
                        raise ValueError(f"{k} invalide")
                    c[k] = v
            if "validated" in change:
                c["validated"] = bool(change["validated"])
            if "refs" in change:
                if not isinstance(change["refs"], list):
                    raise ValueError("refs invalide")
                c["refs"] = change["refs"][-MAX_REFS:]
            c["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
            self.data[key] = c
            tmp = f"{self.path}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)   # jamais de fichier à moitié écrit
