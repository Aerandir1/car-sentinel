"""État partagé entre le thread d'acquisition et le serveur HTTP : dernières valeurs, min/max, voiture en cours.

Les mesures de chaque voiture sont dans son module (clio.py, fiesta.py) : METRICS, DERIVED.
"""

import csv
import json
import math
import threading
import time
from collections import deque

import donnees
import trajets
import voitures

BARO_DEFAULT_KPA = 101.3
L100_MIN_KMH = 5     # en dessous (arrêt, manœuvre), pas de L/100 km
L100_MAX = 99.0      # pied au plancher en 1re : plafonné comme sur l'ordinateur de bord
TRIP_GAP_MS = 10_000  # plus longtemps entre deux mesures : le trou ne compte pas (comme trajets.RUN_GAP_S)
LABEL_EVERY_MS = 1000  # étiquettes « sport », « road », « section » enregistrées une fois par seconde (lecture)
UNDO_S = 15           # un appui sur un bouton de tronçon s'annule pendant ce temps
# GPS (tablette Android : gps.py) : position enregistrée comme les mesures, une par seconde au plus
GPS_KEYS = ("gps_lat", "gps_lon", "gps_speed", "gps_alt", "gps_head", "gps_acc")
GPS_EVERY_MS = 900
GPS_RECENT_MS = 5000      # position plus ancienne : GPS perdu (témoin du dashboard)
# Points de passage (réglés sur la page du serveur, synchro.sync_waypoints) : en y passant dans le bon sens, un
# nouveau tronçon de leur type commence, comme un appui sur son bouton
WAYPOINT_RADIUS_M = 40    # rayon par défaut
WAYPOINT_HEADING = 60     # écart toléré (°) avec le sens de passage du point
WAYPOINT_REARM = 2.5      # ressorti à plus de 2,5 rayons : le point peut se déclencher à nouveau
MANUAL_PRIORITY_S = 60    # bouton appuyé depuis moins d'une minute : le point de passage ne change rien
# Bouchons détectés en roulant, sur un tronçon Route ou Voie rapide : sur JAM_WINDOW_S, sous JAM_KMH au moins
# JAM_SLOW du temps, en avançant (au-dessus de 3 km/h) au moins JAM_MOVING du temps. Un feu rouge (arrêt puis
# départ rapide) ou une pause (station, parking) n'en sont pas. Fini quand on roule au-dessus de JAM_END_KMH
# pendant JAM_END_S : le type d'avant reprend. Début et fin datés du vrai début du ralentissement et de la
# reprise (étiquettes corrigées après coup). Un appui sur un bouton dans la minute passe devant
JAM_KMH, JAM_WINDOW_S, JAM_SLOW, JAM_MOVING = 30, 90, 0.9, 0.3
JAM_END_KMH, JAM_END_S = 45, 30
JAM_AUTO, JAM_END_AUTO = "bouchons détectés", "fin des bouchons"


def distance_m(lat1, lon1, lat2, lon2):
    """Distance au sol entre deux positions (m), assez juste à l'échelle d'un carrefour."""
    k = math.cos(math.radians((lat1 + lat2) / 2))
    return 6_371_000 * math.radians(math.hypot(lat2 - lat1, (lon2 - lon1) * k))

# Réglages modifiables à chaud depuis l'onglet Réglages : clé -> (min, max)
SETTINGS_LIMITS = {
    "max_hz": (0.5, 50.0),   # fréquence max de la boucle d'acquisition
    "delay": (0.0, 1.0),     # pause en s entre deux requêtes OBD
    "log_hz": (0.2, 50.0),   # fréquence du CSV
}
# Prix du carburant en €/L (coût des trajets), un par voiture (gazole, E85…) : « fuel_price » vu de la page est
# celui de la voiture en cours, rangé sous « fuel_price_<voiture> » dans data/reglages.json. Synchronisé avec le
# serveur (synchro.sync_prices, push_price) : price_sync garde, par voiture, l'instant du réglage (« t », ms), qui
# l'a réglé (« source » : nom d'un dashboard, ou « page du serveur ») et s'il reste à envoyer (« pending »)
PRICE_LIMITS = (0.0, 10.0)
PRICE_KEYS = tuple(f"fuel_price_{c}" for c in voitures.CARS)
# Texte : clé -> longueur max. Serveur (clio-sentinel-server, adresses séparées par des virgules) et nom de ce
# poste sur sa page : vides tant qu'ils ne sont pas changés ici, SERVEUR et NOM_PC de car.conf s'appliquent
SETTINGS_TEXT = {"server": 300, "server_name": 60}
# Réglages gardés d'un lancement à l'autre (les autres viennent des options). car : dernière voiture vue
SETTINGS_SAVED = (*PRICE_KEYS, *SETTINGS_TEXT, "car", "price_sync")
# Mode rapide python-obd : pas d'écho, nombre de réponses attendu → requêtes plus courtes.
# Certains clones d'ELM327 le gèrent mal. Changer ce réglage relance la connexion.
# Mode démo : valeurs simulées (demo.py) à la place de l'ECU, enregistrées dans une base à part (<voiture>-demo.db).
SETTINGS_FLAGS = ("fast", "demo")


class Garage:
    """Fichiers d'une voiture dans data/ : bases (vraie et démo) et étalonnage. Les bases sont ouvertes à la
    première utilisation de la voiture (une voiture jamais branchée n'a pas de base vide)."""

    def __init__(self, car, data_dir, version, no_db=False):
        import etalonnage
        self.car, self.version, self.no_db = car, version, no_db
        self.db = None if no_db else data_dir / f"{car.ID}.db"
        self.demo_db = None if no_db else data_dir / f"{car.ID}-demo.db"
        self.calib = etalonnage.Store(data_dir / f"etalonnage-{car.ID}.json", car)
        self.recorder = self.demo_recorder = None
        self.lock = threading.Lock()

    def open(self):
        """Crée les bases au besoin et lance leur enregistrement ; renvoie self."""
        with self.lock:
            if self.db and self.recorder is None:
                self.recorder = donnees.Recorder(self.db, self.version, self.car)
                self.demo_recorder = donnees.Recorder(self.demo_db, self.version, self.car)
        return self

    def exists(self):
        return bool(self.db) and self.db.exists()


class State:
    """Dernières valeurs + min/max, partagé entre le thread de lecture et le serveur HTTP."""

    def __init__(self):
        self.lock = threading.Lock()
        self.values = {}
        self.times = {}       # horodatage (ms UTC) de la dernière valeur de chaque métrique
        self.recorder = None       # donnees.Recorder : chaque valeur part aussi dans la base
        self.demo_recorder = None  # la même chose pour le mode démo, dans une base à part
        self.mins = {}
        self.maxs = {}
        self.supported = []
        self.unsupported = []
        self.sources = {}     # clé → requête qui donne la mesure : {"kind": "obd" | "maker", "cmd": "01 5C"}
        self.car = voitures.get(None)   # module de la voiture en cours (clio, fiesta) : voir set_car
        self.garages = {}     # voiture → Garage (bases, étalonnage), voir set_car
        self.calib = None     # etalonnage.Store de la voiture en cours : gain et décalage des mesures constructeur
        self.raw_keys = []    # mesures constructeur qui répondent : valeur brute dans « brut.<clé> » (onglet Étalonnage)
        self.meta_rev = 0     # change quand la liste des tuiles change sans reconnexion (mesure validée)
        self.status = "démarrage"
        self.source = ""
        self.vin = None
        self.adapter = "absent"   # ELM327 : "absent", "elm" (branché, calculateur muet), "car" (calculateur répond)
        # Témoins du dashboard, plus détaillés (adapter, lui, part tel quel au serveur) :
        #   elm : "recherche", "ok", "absent" (aucun ELM327), "refuse" (port interdit),
        #         "occupe" (réservé par un autre programme, un scan par exemple), "ko" (ne répond pas bien), "demo",
        #         "reconnexion" (perdu en pleine lecture : il redémarre quand la tension de la prise OBD saute,
        #         au démarrage ou à l'arrêt du moteur, et revient en quelques secondes)
        #   ecu : "off" (pas d'adaptateur), "recherche" (essai des protocoles), "ok", "muet" (contact coupé), "demo"
        self.elm, self.ecu = "recherche", "off"
        self.lost_ms = None   # adaptateur perdu en pleine lecture à cet instant (ms), None une fois revenu
        self.elm_name = None  # réponse à ATI, ex. « ELM327 v1.5 »
        self.port = None
        self.baud = None      # débit de l'ELM327, repris par le diagnostic avec le port (diagnostic.py)
        self.protocol = None  # protocole OBD du calculateur, ex. « ISO 15765-4 (CAN 11/500) »
        self.fuel_type = None # type de carburant annoncé (01 51), ex. « Bifuel running Ethanol »
        self.trip = None      # identifiant du trajet en cours (mis à jour par trajets.py)
        # Carburant et distance du trajet en cours : la fiche calculée par trajets.py toutes les 10 s, plus ce qui
        # a été lu depuis (increments), pour un coût qui monte en direct
        self.trip_base = None     # {"id", "end_ms", "fuel_l", "distance_km"}
        self.increments = deque() # (t_ms, litres, km) depuis la dernière fiche
        self.integ = {}           # "fuel" / "speed" → (t_ms, valeur) de la dernière mesure intégrée
        self.settings_file = None # data/reglages.json (SETTINGS_SAVED)
        self.hz = 0.0
        self.ping_ms = None   # temps de réponse moyen d'une requête au calculateur (requetes.py)
        self.sync = None      # dernier essai de synchro vers clio-sentinel-server (voir synchro.py)
        self.settings = {}    # réglages donnés (options, onglet Réglages, data/reglages.json)
        self.defaults = {}    # valeurs de car.conf tant que le réglage n'a pas été changé ici (jamais enregistrées)
        # Onglet Diagnostic (diagnostic.py) : il demande l'adaptateur (diag_want), l'acquisition le lui laisse
        # (port_free) et reprend quand il a fini
        self.diag_want = threading.Event()
        self.port_free = threading.Event()
        self.diag = None      # diagnostic.Diag
        # Étiquette pour l'apprentissage (bouton Sport du mode Conduite) : 1 conduite sportive, 0 normale. Enregistrée
        # dans samples sous la clé « sport », comme une mesure ; repart à 0 à chaque lancement
        self.sport = 0
        self.label_ms = 0     # dernier enregistrement des étiquettes
        # Tronçon en cours (boutons de type de route du mode Conduite, voir trajets.sections) :
        # {"n", "road", "t_ms", "trip", "prev"}, prev : le tronçon d'avant (Annuler). Fini avec le trajet
        self.section = None
        self.section_seen = set()   # trajets dont le dernier tronçon a été repris de la base (relance en route)
        self.manual_ms = 0          # dernier appui sur un bouton de tronçon (priorité sur les points de passage)
        self.gps = None             # dernière position : {"t", "lat", "lon", "speed", "alt", "head", "acc"}
        self.gps_ms = 0             # dernière position enregistrée
        self.waypoints = []         # points de passage : [{"id", "lat", "lon", "head", "radius", "road", "name"}]
        self.waypoints_file = None  # data/points.json : les derniers reçus du serveur (pour rouler hors réseau)
        self.wp_inside = set()      # points où l'on est passé, pas encore ressorti
        self.speed_hist = deque()   # (t_ms, km/h) des 2 dernières minutes : bouchons détectés en roulant
        self.jam_prev = None        # type de route d'avant les bouchons (repris à leur fin)
        self.jam_check_ms = 0

    # --- Voiture en cours ---

    def set_car(self, car_id, why=""):
        """Passe à la voiture car_id (reconnue au VIN, choisie dans Réglages, ou au lancement) : ses bases, son
        étalonnage, son prix du carburant. Gardée comme dernière voiture vue. Renvoie True si elle a changé."""
        car = voitures.get(car_id)
        garage = self.garages[car.ID].open() if car.ID in self.garages else None
        with self.lock:
            changed = car is not self.car or self.calib is None
            self.car = car
            if garage:
                self.recorder, self.demo_recorder, self.calib = garage.recorder, garage.demo_recorder, garage.calib
            if changed:
                self.meta_rev += 1   # le dashboard recharge la liste des tuiles et s'habille pour cette voiture
                self.supported, self.unsupported, self.sources, self.raw_keys = [], [], {}, []
        if self.settings.get("car") != car.ID:
            self.set_settings({"car": car.ID})
        if changed and why:
            print(f"Voiture : {car.NAME} ({why})")
        return changed

    def car_db(self, demo=None):
        """Base affichée de la voiture en cours (celle de la démo en mode démo), None avec --no-db."""
        g = self.garages.get(self.car.ID)
        if g is None:
            return None
        return g.demo_db if (self.settings.get("demo") if demo is None else demo) else g.db

    def get_settings(self):
        """Réglages, plus fuel_price : celui de la voiture en cours."""
        with self.lock:
            out = {**self.defaults, **self.settings}
            out["fuel_price"] = out.get(f"fuel_price_{self.car.ID}")
            return out

    def set_settings(self, new):
        new = dict(new)
        if "fuel_price" in new:   # prix réglé sur la page : celui de la voiture en cours
            new[f"fuel_price_{self.car.ID}"] = new.pop("fuel_price")
        if "car" in new and new["car"] not in voitures.CARS:
            raise ValueError("voiture inconnue")
        me = self.name() if any(k in new for k in PRICE_KEYS) else None   # avant le verrou (name le prend aussi)
        with self.lock:
            for key, (lo, hi) in SETTINGS_LIMITS.items():
                if key in new and new[key] is not None:
                    self.settings[key] = min(hi, max(lo, float(new[key])))
            sync = dict(self.settings.get("price_sync") or {})
            for key in PRICE_KEYS:
                if key in new and new[key] is not None:
                    self.settings[key] = min(PRICE_LIMITS[1], max(PRICE_LIMITS[0], float(new[key])))
                    # réglé ici : à envoyer au serveur, daté de maintenant, au nom de ce dashboard
                    sync[key[len("fuel_price_"):]] = {"t": donnees.now_ms(), "pending": True, "source": me}
            if any(k in new for k in PRICE_KEYS):
                self.settings["price_sync"] = sync
            if "car" in new:
                self.settings["car"] = new["car"]
            for key in SETTINGS_FLAGS:
                if key in new:
                    self.settings[key] = bool(new[key])
            for key, n in SETTINGS_TEXT.items():
                if key in new:
                    self.settings[key] = str(new[key] or "").strip()[:n]
            saved = {k: self.settings[k] for k in SETTINGS_SAVED if k in self.settings}
        if self.settings_file and any(k in new for k in SETTINGS_SAVED):
            tmp = self.settings_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.settings_file)
        return self.get_settings()

    def load_settings(self, path):
        """Réglages gardés (prix du carburant…) : lus au lancement, réécrits à chaque changement."""
        self.settings_file = path
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        car = saved.get("car") if saved.get("car") in voitures.CARS else None
        # Réglages d'avant la fusion (clio-sentinel, fiesta-sentinel) : un seul prix, celui de cette voiture-là
        if isinstance(saved.get("fuel_price"), (int, float)) and f"fuel_price_{car or voitures.DEFAULT}" not in saved:
            saved[f"fuel_price_{car or voitures.DEFAULT}"] = saved["fuel_price"]
        with self.lock:
            for key in SETTINGS_SAVED:
                if key in PRICE_KEYS and isinstance(saved.get(key), (int, float)):
                    self.settings[key] = min(PRICE_LIMITS[1], max(PRICE_LIMITS[0], float(saved[key])))
                elif key in SETTINGS_TEXT and isinstance(saved.get(key), str):
                    self.settings[key] = saved[key][:SETTINGS_TEXT[key]]
            if car:
                self.settings["car"] = car
            if isinstance(saved.get("price_sync"), dict):
                self.settings["price_sync"] = {c: v for c, v in saved["price_sync"].items()
                                               if c in voitures.CARS and isinstance(v, dict)}

    def _prices_unlocked(self):
        """Prix de chaque voiture (voir prices), sous self.lock."""
        sync_ok = (self.sync or {}).get("ok") is True
        sync = self.settings.get("price_sync") or {}
        out = []
        for car_id in voitures.CARS:
            price, s = self.settings.get(f"fuel_price_{car_id}"), sync.get(car_id) or {}
            state = "aucun" if price is None else "attente" if s.get("pending") else "serveur" if sync_ok else "hors_ligne"
            out.append({"car": car_id, "price": price, "t": s.get("t"), "source": s.get("source"), "state": state,
                        "current": car_id == self.car.ID})
        return out

    def name(self):
        """Nom de ce dashboard (NOM_PC, onglet Réglages, sinon nom de la machine) : sur le serveur et ses prix."""
        import socket
        return self.get_settings().get("server_name") or socket.gethostname()

    def price_sync(self):
        """Par voiture : (prix ici, {"t", "pending", "source"})."""
        with self.lock:
            sync = self.settings.get("price_sync") or {}
            return {c: (self.settings.get(f"fuel_price_{c}"), dict(sync.get(c) or {})) for c in voitures.CARS}

    def prices(self):
        """Prix de chaque voiture pour la page : prix, réglé quand et par qui, état de la synchro
        (« serveur » : reçu par le serveur et à jour, « attente » : réglé ici, pas encore envoyé, « hors_ligne » :
        serveur injoignable ou pas réglé, dernier prix connu, « aucun »), et si c'est la voiture en cours."""
        with self.lock:
            out = self._prices_unlocked()
        for p in out:
            car = voitures.get(p["car"])
            p.update(name=car.NAME, fuel=car.FUEL)
        return out

    def price_synced(self, car_id, price=None, t=None, source=None):
        """Prix envoyé au serveur (price None, t : son instant) ou repris du serveur (price, t, source) : plus rien
        à envoyer."""
        with self.lock:
            sync = dict(self.settings.get("price_sync") or {})
            entry = dict(sync.get(car_id) or {})
            entry["pending"] = False
            if price is not None:
                self.settings[f"fuel_price_{car_id}"] = min(PRICE_LIMITS[1], max(PRICE_LIMITS[0], float(price)))
            if t is not None:
                entry["t"] = t
            if source is not None:
                entry["source"] = source
            sync[car_id] = entry
            self.settings["price_sync"] = sync
            saved = {k: self.settings[k] for k in SETTINGS_SAVED if k in self.settings}
        if self.settings_file:
            tmp = self.settings_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
            tmp.replace(self.settings_file)

    def default_settings(self, defaults):
        """Valeurs prises tant que le réglage n'a jamais été changé dans l'onglet Réglages (pas enregistrées)."""
        with self.lock:
            self.defaults.update(defaults)

    def update(self, key, value, t_ms=None):
        """Nouvelle valeur, horodatée à l'appel (juste après la réponse du calculateur) sauf t_ms donné."""
        t_ms = t_ms or donnees.now_ms()
        rec = self.active_recorder()
        if rec:
            rec.add(key, t_ms, value)
        with self.lock:
            self.values[key] = value
            self.times[key] = t_ms
            if value is None:
                return
            self.mins[key] = min(self.mins.get(key, value), value)
            self.maxs[key] = max(self.maxs.get(key, value), value)

    def update_raw(self, key, raw):
        """Valeur brute d'une mesure constructeur : enregistrée telle quelle, et étalonnée si la mesure est validée."""
        self.update("brut." + key, raw)
        if self.calib is None:
            return
        if self.calib.get(key)[2]:
            self.update(key, None if raw is None else self.calib.apply(key, raw))

    def calib_changed(self):
        with self.lock:
            self.meta_rev += 1

    def shown(self, key):
        """Tuile affichée : mesure lue, et validée si c'est une mesure constructeur."""
        if key not in self.supported:
            return False
        src = self.sources.get(key) or {}
        return src.get("kind") != "maker" or self.calib is None or self.calib.get(key)[2]

    def set_sport(self, on):
        """Bouton Sport : enregistré tout de suite, puis chaque seconde par derive."""
        with self.lock:
            self.sport = 1 if on else 0
        self.record_labels(force=True)

    def record_labels(self, force=False, t=None):
        t = t or donnees.now_ms()
        with self.lock:
            if not force and t - self.label_ms < LABEL_EVERY_MS:
                return
            self.label_ms = t
            sec = self.section
        self.update("sport", self.sport, t)
        if sec:
            self.update("road", sec["road"], t)
            self.update("section", sec["n"], t)

    def new_section(self, road, auto=None, t_ms=None):
        """Bouton de tronçon : un nouveau tronçon de ce type commence maintenant (numéro suivant dans le trajet).
        auto : ce qui l'a déclenché, point de passage ou bouchons (None : appui sur le bouton). t_ms : début plus
        tôt (bouchons reconnus après coup) : les étiquettes déjà enregistrées depuis sont corrigées."""
        if road not in trajets.ROAD_LABELS:
            raise ValueError("type de route inconnu")
        now = donnees.now_ms()
        with self.lock:
            prev_start = self.section["t_ms"] if self.section else None
        t = now if t_ms is None else max(min(t_ms, now), (prev_start or 0) + 1)
        if auto is None:
            self.manual_ms = t
        with self.lock:
            prev, trip = self.section, self.trip
        if prev:
            n, trip = prev["n"] + 1, prev["trip"]
        else:   # premier appui du trajet, ou relance du script en route : les appuis déjà dans la base comptent
            n = 1 + self._presses_in_trip(trip, t)
        with self.lock:
            self.section = {"n": n, "road": road, "t_ms": t, "trip": trip, "prev": prev, "auto": auto, "set_ms": now}
        self.update("troncon", road, t)
        rec = self.active_recorder()
        if t < now and rec:   # début daté d'avant : étiquettes de la période reprises
            rec.relabel("road", t, now, road)
            rec.relabel("section", t, now, n)
        self.record_labels(force=True, t=max(t, now))

    def undo_section(self):
        """Annule le dernier appui (moins de UNDO_S secondes) : effacé de la base, le tronçon d'avant reprend (ses
        étiquettes « road » et « section » couvrent aussi le temps de l'appui annulé)."""
        t = donnees.now_ms()
        with self.lock:
            sec = self.section
            if not sec or t - sec.get("set_ms", sec["t_ms"]) > UNDO_S * 1000:
                return False
            self.section = sec["prev"]
        rec = self.active_recorder()
        if rec:
            rec.relabel("troncon", sec["t_ms"], t)
            rec.relabel("road", sec["t_ms"], t, sec["prev"] and sec["prev"]["road"])
            rec.relabel("section", sec["t_ms"], t, sec["prev"] and sec["prev"]["n"])
        self.record_labels(force=True, t=t + 1)
        return True

    def _presses_in_trip(self, trip, t):
        db_path = self.car_db()
        if not db_path or not db_path.exists():
            return 0
        db = donnees.connect(db_path)
        try:
            row = trip and db.execute("SELECT start_ms FROM trips WHERE id = ?", (trip,)).fetchone()
            if row:
                lo = row[0] - trajets.SECTION_EARLY_S * 1000
            else:   # trajet pas encore calculé (premières secondes) : depuis la fin du dernier trajet fini
                lo = db.execute("SELECT coalesce(max(end_ms), 0) + 1 FROM trips WHERE closed = 1").fetchone()[0]
            return len(trajets.presses(db, lo, t))
        finally:
            db.close()

    def _resume_section(self, row):
        """Relance du script en plein trajet : reprend le dernier tronçon enregistré (une fois par trajet)."""
        self.section_seen.add(row["id"])
        db_path = self.car_db()
        if not db_path:
            return
        db = donnees.connect(db_path)
        try:
            secs = trajets.sections(db, row["start_ms"], 2**62, detail=False)
        finally:
            db.close()
        if secs:
            last = secs[-1]
            with self.lock:
                if self.section is None:
                    self.section = {"n": last["n"], "road": last["road"], "t_ms": last["start_ms"],
                                    "trip": row["id"], "prev": None}

    def section_info(self):
        """Tronçon en cours pour la page. Appelé sous self.lock."""
        sec = self.section
        if not sec:
            return None
        return {"n": sec["n"], "road": sec["road"], "label": trajets.ROAD_LABELS[sec["road"]], "t_ms": sec["t_ms"],
                "undo_until": sec.get("set_ms", sec["t_ms"]) + UNDO_S * 1000, "auto": sec.get("auto")}

    # --- GPS et points de passage ---

    def gps_fix(self, lat, lon, speed=None, alt=None, head=None, acc=None, t_ms=None):
        """Nouvelle position (speed en km/h, head : cap en °, acc : précision en m). Enregistrée une fois par
        seconde au plus ; déclenche les points de passage."""
        t = t_ms or donnees.now_ms()
        with self.lock:
            self.gps = {"t": t, "lat": lat, "lon": lon, "speed": speed, "alt": alt, "head": head, "acc": acc}
            record = t - self.gps_ms >= GPS_EVERY_MS
            if record:
                self.gps_ms = t
        if record:
            for key, v, nd in zip(GPS_KEYS, (lat, lon, speed, alt, head, acc), (6, 6, 1, 1, 0, 1)):
                if v is not None:
                    self.update(key, round(v, nd), t)
        self._check_waypoints(lat, lon, speed, head)

    def _check_waypoints(self, lat, lon, speed, head):
        for wp in list(self.waypoints):
            r = wp.get("radius") or WAYPOINT_RADIUS_M
            d = distance_m(lat, lon, wp["lat"], wp["lon"])
            if d > WAYPOINT_REARM * r:
                self.wp_inside.discard(wp["id"])
                continue
            if d > r or wp["id"] in self.wp_inside:
                continue
            # Sens de passage : seulement en roulant (le cap d'une voiture arrêtée ne veut rien dire)
            if wp.get("head") is not None and head is not None and (speed or 0) > 5:
                if abs((head - wp["head"] + 180) % 360 - 180) > WAYPOINT_HEADING:
                    continue
            self.wp_inside.add(wp["id"])
            sec = self.section
            if donnees.now_ms() - self.manual_ms < MANUAL_PRIORITY_S * 1000 or (sec and sec["road"] == wp["road"]):
                continue
            print(f"Point de passage « {wp.get('name') or wp['id']} » : tronçon {trajets.ROAD_LABELS[wp['road']]}")
            self.new_section(wp["road"], auto=wp.get("name") or "point de passage")

    def _check_jam(self):
        """Bouchons détectés à la vitesse lue (appelé à chaque tour de lecture, vérifié toutes les 2 s)."""
        with self.lock:
            t, v, sec = self.times.get("speed"), self.values.get("speed"), self.section
        if t is None or v is None:
            return
        h = self.speed_hist
        if not h or h[-1][0] != t:
            h.append((t, v))
        while h and h[0][0] < t - 120_000:
            h.popleft()
        if t - self.jam_check_ms < 2000 or not sec or donnees.now_ms() - self.manual_ms < MANUAL_PRIORITY_S * 1000:
            return
        self.jam_check_ms = t

        def window(seconds):
            """[(t, km/h)] depuis seconds (et depuis le début du tronçon), None tant que la fenêtre n'est pas pleine."""
            pts = [(tt, x) for tt, x in h if tt >= max(t - seconds * 1000, sec["t_ms"])]
            return pts if pts and t - pts[0][0] >= (seconds - 5) * 1000 else None

        def since(pts, ok):
            """Début de la dernière suite de lectures qui vérifient ok (le vrai début du ralentissement…)."""
            start = pts[-1][0]
            for tt, x in reversed(pts):
                if not ok(x):
                    break
                start = tt
            return start

        if sec["road"] in (2, 3):
            pts = window(JAM_WINDOW_S)
            if pts and sum(x < JAM_KMH for _t, x in pts) >= JAM_SLOW * len(pts) \
                    and sum(x >= 3 for _t, x in pts) >= JAM_MOVING * len(pts):
                self.jam_prev = sec["road"]
                start = since(pts, lambda x: x < JAM_KMH + 10)
                print(f"Bouchons détectés depuis {(t - start) // 1000} s")
                self.new_section(4, auto=JAM_AUTO, t_ms=start)
        elif sec["road"] == 4 and sec.get("auto") == JAM_AUTO:
            pts = window(JAM_END_S)
            if pts and all(x > JAM_END_KMH for _t, x in pts[len(pts) // 3:]) and sum(x for _t, x in pts) > JAM_END_KMH * len(pts):
                road = self.jam_prev or 2
                print(f"Fin des bouchons : {trajets.ROAD_LABELS[road]}")
                self.new_section(road, auto=JAM_END_AUTO, t_ms=since(pts, lambda x: x >= JAM_KMH))

    def load_waypoints(self, path):
        self.waypoints_file = path
        try:
            self.set_waypoints(json.loads(path.read_text(encoding="utf-8")), save=False)
        except (OSError, ValueError):
            pass

    def set_waypoints(self, points, save=True):
        """Points de passage reçus du serveur : gardés s'ils sont valides, et enregistrés pour rouler hors réseau."""
        out = []
        for p in points if isinstance(points, list) else []:
            try:
                wp = {"id": str(p["id"]), "lat": float(p["lat"]), "lon": float(p["lon"]), "road": int(p["road"]),
                      "head": None if p.get("head") is None else float(p["head"]),
                      "radius": float(p.get("radius") or WAYPOINT_RADIUS_M), "name": str(p.get("name") or "")[:80]}
            except (KeyError, TypeError, ValueError):
                continue
            if wp["road"] in trajets.ROAD_LABELS:
                out.append(wp)
        changed = out != self.waypoints
        self.waypoints = out
        if save and changed and self.waypoints_file:
            tmp = self.waypoints_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(out, indent=1), encoding="utf-8")
            tmp.replace(self.waypoints_file)
        return changed

    def derive(self):
        self.record_labels()
        self._check_jam()
        with self.lock:
            m = self.values.get("map")
            t_map = self.times.get("map")
            baro = self.values.get("baro") or BARO_DEFAULT_KPA
            fuel, t_fuel = self.values.get("fuel"), self.times.get("fuel")
            speed = self.values.get("speed")
            new_fuel = t_fuel is not None and t_fuel != self.integ.get("fuel", (None,))[0]
            self._integrate()
        if m is not None:
            self.update("boost", round((m - baro) / 100, 3), t_map)   # même instant que la pression d'admission
        if new_fuel:   # une valeur par nouvelle mesure de conso, au même instant
            if fuel is not None and speed is not None and speed >= L100_MIN_KMH:
                self.update("l100", round(min(L100_MAX, 100 * fuel / speed), 2), t_fuel)
            else:   # à l'arrêt : rien d'enregistré (ce n'est pas une absence de réponse), tuile vide
                with self.lock:
                    self.values["l100"] = None

    def _integrate(self):
        """Carburant (L/h) et vitesse (km/h) intégrés depuis la dernière mesure, comme trajets.py (trapèzes,
        trous de plus de 10 s ignorés). Appelé sous self.lock."""
        for key in ("fuel", "speed"):
            t, v = self.times.get(key), self.values.get(key)
            if t is None or v is None:
                continue
            prev = self.integ.get(key)
            self.integ[key] = (t, v)
            if prev and prev[0] is not None and 0 < t - prev[0] <= TRIP_GAP_MS:
                amount = (prev[1] + v) / 2 * (t - prev[0]) / 3_600_000
                self.increments.append((t, amount, 0.0) if key == "fuel" else (t, 0.0, amount))
        while len(self.increments) > 20_000:   # pas de trajet (pas de base) pendant longtemps
            self.increments.popleft()

    def set_trip(self, row):
        """Fiche du trajet en cours (trajets.update), None sans trajet en cours."""
        resume = False
        with self.lock:
            # Tronçon : fini avec son trajet. Un appui sans trajet en cours (premières secondes, trajet pas encore
            # calculé ; ou pas de calculateur) attend le trajet qui apparaît, au plus TRIP_GAP_S
            sec = self.section
            if sec and sec["trip"] is None:
                if row:
                    sec["trip"] = row["id"]
                elif donnees.now_ms() - sec["t_ms"] > trajets.TRIP_GAP_S * 1000:
                    self.section = None
            elif sec and (row is None or row["id"] != sec["trip"]):
                self.section = None
            resume = row is not None and self.section is None and row["id"] not in self.section_seen
            self.trip = row["id"] if row else None
            self.trip_base = row and {k: row[k] for k in ("id", "end_ms", "fuel_l", "distance_km")}
            if row:
                while self.increments and self.increments[0][0] <= row["end_ms"]:
                    self.increments.popleft()
        if resume:
            self._resume_section(row)

    def _trip_live(self):
        """Carburant et distance du trajet en cours, à jour de la dernière mesure. Appelé sous self.lock."""
        b = self.trip_base
        if not b:
            return None
        fuel = (b["fuel_l"] or 0.0) + sum(i[1] for i in self.increments if i[0] > b["end_ms"])
        km = (b["distance_km"] or 0.0) + sum(i[2] for i in self.increments if i[0] > b["end_ms"])
        has_fuel = b["fuel_l"] is not None or any(i[1] for i in self.increments)
        return {"id": b["id"], "fuel_l": round(fuel, 4) if has_fuel else None, "distance_km": round(km, 3)}

    def start_session(self, source, vin, metrics):
        """Connexion au calculateur établie : nouvelle session dans la base."""
        self.source, self.vin = source, vin
        rec = self.active_recorder()
        if rec:
            rec.start_session(source, vin, metrics)

    def active_recorder(self):
        """Base où vont les mesures : celle de la démo en mode démo, la vraie sinon."""
        return self.demo_recorder if self.settings.get("demo") else self.recorder

    def clear_live(self):
        """Oublie les dernières valeurs et les min/max (entrée et sortie du mode démo)."""
        with self.lock:
            for d in (self.values, self.times, self.mins, self.maxs, self.integ):
                d.clear()
            self.increments.clear()
            self.trip, self.trip_base = None, None   # l'autre base : sa fiche arrive au prochain passage de trajets.py
            self.section = None
            self.hz, self.ping_ms = 0.0, None

    def reset_minmax(self):
        with self.lock:
            self.mins.clear()
            self.maxs.clear()

    def snapshot(self):
        with self.lock:
            return {
                "t": time.time(),
                "status": self.status,
                "hz": round(self.hz, 1),
                "ping_ms": None if self.ping_ms is None else round(self.ping_ms),
                "values": dict(self.values),
                "ts": dict(self.times),
                "trip": self.trip,
                "sport": self.sport,
                "section": self.section_info(),
                "gps": self.gps and {"t": self.gps["t"], "acc": self.gps["acc"],
                                     "ok": time.time() * 1000 - self.gps["t"] < GPS_RECENT_MS},
                "waypoints": len(self.waypoints),
                "trip_live": self._trip_live(),
                "fuel_price": self.settings.get(f"fuel_price_{self.car.ID}"),
                "price": next((p for p in self._prices_unlocked() if p["current"]), None),
                "car": self.car.ID,
                "meta_rev": self.meta_rev,
                "sync": self.sync,
                "link": {"elm": self.elm, "ecu": self.ecu, "name": self.elm_name, "port": self.port,
                         "protocol": self.protocol, "vin": self.vin,
                         "fuel_type": self.fuel_type},
                "min": dict(self.mins),
                "max": dict(self.maxs),
            }

    def meta(self):
        car = self.car
        shown = [k for k, *_ in car.METRICS if self.shown(k)]
        with self.lock:
            metrics = [
                {"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold,
                 "source": self.sources.get(k)}
                for k, lbl, u, _cmd, _conv, rng, warn, cold in car.METRICS
                if k in shown
            ]
            for d in car.DERIVED:
                if all(self.shown(k) for k in d["needs"]):
                    metrics.append({**derived_meta(d), "source": {"kind": "calc", "cmd": None}})
            return {
                "car": voitures.info(car),
                "cars": voitures.choices(),
                "roads": [{"code": c, "key": k, "label": lbl} for c, k, lbl in trajets.ROADS],
                "source": self.source,
                "vin": self.vin,
                "metrics": metrics,
                "unsupported": [lbl for k, lbl, *_ in car.METRICS if k in self.unsupported],
                # Mesures constructeur qui répondent mais pas encore validées (onglet Étalonnage)
                "to_calibrate": [lbl for k, lbl, *_ in car.METRICS if k in self.raw_keys and k not in shown],
            }


def derived_meta(d):
    """Valeur calculée, au format des METRICS (sans seuils)."""
    return {k: d[k] for k in ("key", "label", "unit", "range")} | {"warn": None, "cold": None}


class CsvLogger:
    def __init__(self, path, keys):
        self.keys = keys
        self.next_t = 0.0
        # Ajout à la fin : chaque reconnexion au calculateur reprend le même fichier
        self.f = open(path, "a", newline="", encoding="utf-8")
        self.w = csv.writer(self.f)
        if self.f.tell() == 0:
            self.w.writerow(["timestamp"] + keys)

    def maybe_write(self, state):
        now = time.time()
        if now < self.next_t:
            return
        self.next_t = now + 1.0 / state.get_settings()["log_hz"]
        snap = state.snapshot()["values"]
        self.w.writerow([f"{now:.3f}"] + [snap.get(k, "") for k in self.keys])
        self.f.flush()

    def close(self):
        self.f.close()
