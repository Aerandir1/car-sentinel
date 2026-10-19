"""Étalonnage des mesures constructeur (Ford, Renault) : gain et décalage réglés dans l'onglet Étalonnage.

valeur = brut * gain + décalage. Sans réglage, ceux du FALLBACK de la voiture (fiesta.py, clio.py). Une mesure
candidate n'est affichée en tuile qu'une fois validée (celles de son TRUSTED le sont d'office).
Rangé dans data/etalonnage-<voiture>.json, avec les références notées (valeur lue sur l'ordinateur de bord) :

    {"fuel": {"gain": 0.0042, "offset": 0.0, "validated": true, "updated": "2026-10-13 15:02:11",
              "refs": [{"t0": …, "t1": …, "ref": 5.4, "unit": "L/100 km", "a": …, "b": …}]}}

Il part aussi sur clio-sentinel-server (synchro.py) : un nouveau client (autre PC, tablette réinstallée) le
récupère, et un étalonnage fait sur un client arrive sur les autres. Fusion mesure par mesure : la plus récente
(« updated ») gagne.
"""

import json
import math
import os
import threading
import time

MAX_REFS = 50


class Store:
    def __init__(self, path, car):
        self.path, self.car = path, car
        self.lock = threading.Lock()
        self.data = {}
        try:
            with open(path, encoding="utf-8") as f:
                self.data = {k: v for k, v in json.load(f).items() if k in car.FALLBACK}
        except FileNotFoundError:
            pass
        except (OSError, ValueError) as e:
            print(f"Attention : {path} illisible ({e}) : étalonnage par défaut")

    def get(self, key):
        """(gain, décalage, validé) de la mesure."""
        _did, gain, offset, *_ = self.car.FALLBACK[key]
        with self.lock:
            c = self.data.get(key, {})
        return c.get("gain", gain), c.get("offset", offset), c.get("validated", key in self.car.TRUSTED)

    def apply(self, key, raw):
        """Valeur étalonnée, None si hors de la plage plausible de la mesure."""
        gain, offset, _ = self.get(key)
        v = raw * gain + offset
        lo, hi = self.car.FALLBACK[key][3]
        return round(v, 2) if lo <= v <= hi else None

    def describe(self):
        """Toutes les mesures constructeur de la voiture, pour l'onglet Étalonnage."""
        out = {}
        for key, (did, gain, offset, rng, *_) in self.car.FALLBACK.items():
            g, o, ok = self.get(key)
            with self.lock:
                c = self.data.get(key, {})
            out[key] = {"did": did, "gain": g, "offset": o, "validated": ok, "range": rng,
                        "default_gain": gain, "default_offset": offset, "trusted": key in self.car.TRUSTED,
                        "refs": c.get("refs", []), "updated": c.get("updated")}
        return out

    def save(self, key, change):
        """Enregistre gain, décalage, validation et références (champs absents : inchangés). ValueError si invalide."""
        if key not in self.car.FALLBACK:
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
            self._write()

    def _write(self):
        """Appelé sous self.lock."""
        tmp = f"{self.path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1, ensure_ascii=False)
        os.replace(tmp, self.path)   # jamais de fichier à moitié écrit

    def snapshot(self):
        """Réglages de chaque mesure, tels que rangés dans le fichier (envoyés au serveur)."""
        with self.lock:
            return json.loads(json.dumps(self.data))

    def merge(self, remote):
        """Prend du serveur les mesures plus récentes qu'ici (ou absentes d'ici). Renvoie True si quelque chose a
        changé (le fichier est alors réécrit)."""
        changed = False
        with self.lock:
            for key, c in (remote or {}).items():
                if key not in self.car.FALLBACK or not isinstance(c, dict):
                    continue
                mine = self.data.get(key)
                if mine is None or str(c.get("updated") or "") > str(mine.get("updated") or ""):
                    if c != mine:
                        self.data[key] = c
                        changed = True
            if changed:
                self._write()
        return changed
