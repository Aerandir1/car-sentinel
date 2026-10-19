#!/usr/bin/env python3
"""Dashboard OBD-II temps réel pour la Clio IV dCi 90 et la Ford Fiesta 1.0 EcoBoost (ELM327 + python-obd).

Lit l'ECU en boucle dans un thread et sert dashboard.html + une API JSON sur http://car-sentinel.localhost:8080.
La voiture est reconnue à la connexion (VIN) et le dashboard s'habille pour elle. Aucune dépendance web : tout
marche hors ligne. Réglages dans car.conf (voir car.conf.exemple) ; les options ci-dessous passent devant.

    python src/car_sentinel.py --port /dev/ttyUSB0 --log trajet.csv     (Linux)
    python src/car_sentinel.py --port COM3 --log trajet.csv             (Windows)
"""

import argparse
import os
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path

import config
import diagnostic
import synchro
import trajets
import voitures
from acquisition import obd_loop
from etat import CsvLogger, Garage, State
from reseau import HOST, lan_names
from serveur import QuietServer, make_handler

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"   # bases SQLite, étalonnages, réglages (voir voitures.py)
TRIPS_EVERY_S = 10     # mise à jour des trajets


def version():
    """Commit git du script, enregistré avec chaque session (CAR_VERSION sans dossier .git)."""
    try:
        out = subprocess.run(["git", "-C", str(HERE), "describe", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("CAR_VERSION")


def trips_loop(state):
    """Découpe les mesures en trajets au fil de l'eau (voir trajets.py), dans les bases de chaque voiture (vraie
    et démo) ; le trajet en cours affiché est celui de la voiture en cours."""
    while True:
        try:
            settings = state.get_settings()
            current = None
            for car_id, g in state.garages.items():
                if not g.exists():
                    continue
                price = settings.get(f"fuel_price_{car_id}")
                for db, demo in ((g.db, False), (g.demo_db, True)):
                    trip = trajets.update(db, price=price, car=g.car)
                    if car_id == state.car.ID and demo == bool(settings.get("demo")):
                        current = trip
            state.set_trip(current)
        except Exception:
            traceback.print_exc()
        time.sleep(TRIPS_EVERY_S)


def main():
    if sys.stdout is None:   # pythonw (Windows, service.ps1) : pas de console, le journal va dans logs/car.log
        (HERE / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(HERE / "logs" / "car.log", "a", encoding="utf-8", buffering=1)
        print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="port série de l'ELM327 : /dev/ttyUSB0, COM3… (auto-détection si absent)")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    p.add_argument("--data", metavar="DOSSIER",
                   help="dossier des bases (<voiture>.db), étalonnages et réglages (défaut data/)")
    p.add_argument("--no-db", action="store_true", help="n'enregistre pas les mesures")
    p.add_argument("--voiture", choices=sorted(voitures.CARS),
                   help="voiture au lancement, tant que le VIN n'a pas parlé (défaut : la dernière vue)")
    p.add_argument("--log", metavar="FICHIER.csv", help="enregistre les mesures en CSV")
    p.add_argument("--log-hz", type=float, default=5.0, help="fréquence du CSV (défaut 5 Hz)")
    p.add_argument("--delay", type=float, default=0.0, help="pause en s entre deux requêtes OBD")
    p.add_argument("--max-hz", type=float, default=20.0, help="fréquence max d'acquisition (défaut 20)")
    p.add_argument("--fast", action="store_true", help="mode rapide python-obd (requêtes plus courtes)")
    p.add_argument("--demo", action="store_true", help="démarre en mode démo (trajet simulé, base à part)")
    p.add_argument("--http", type=int, default=8080, help="port local de la page web (défaut 8080)")
    p.add_argument("--bind", default="127.0.0.1", help="adresse d'écoute (défaut 127.0.0.1)")
    p.add_argument("--lan", action="store_true",
                   help="page accessible depuis les autres appareils du réseau (= --bind 0.0.0.0)")
    synchro.add_arguments(p)
    args = p.parse_args(config.argv() + sys.argv[1:])
    if args.lan:
        args.bind = "0.0.0.0"
    on_lan = args.lan or args.bind not in ("127.0.0.1", "localhost", "::1")
    args.dev = "/dev"   # où la détection cherche l'ELM327 sous Linux (Windows : ports COM, acquisition.py)
    if args.server and args.no_db:
        print("Attention : --server envoie les bases, incompatible avec --no-db : synchro désactivée")

    def logger_factory(keys):
        return CsvLogger(args.log, keys) if args.log else None

    data = Path(args.data) if args.data else DATA
    data.mkdir(parents=True, exist_ok=True)
    state = State()
    # Prix du carburant de chaque voiture, dernière voiture vue (onglet Réglages)
    state.load_settings(data / "reglages.json")
    # Serveur de car.conf, tant qu'il n'a pas été changé dans l'onglet Réglages
    state.default_settings({"server": args.server or "", "server_name": args.server_name or ""})
    # Chaque voiture a ses bases et son étalonnage dans data/ (ouverts à sa première utilisation)
    state.garages = {c.ID: Garage(c, data, version(), args.no_db) for c in voitures.CARS.values()}
    state.set_car(args.voiture or state.get_settings().get("car"))
    print(f"Voiture au lancement : {state.car.NAME} (changée dès qu'une autre est reconnue au VIN)")
    if not args.no_db:
        threading.Thread(target=trips_loop, args=(state,), daemon=True).start()
        print(f"Mesures enregistrées dans {data} : une base par voiture"
              f" ({', '.join(f'{c}.db' for c in voitures.CARS)}, démo à part)")
        # Synchro vers clio-sentinel-server, selon l'adresse des réglages (rien tant qu'elle est vide)
        bases = lambda: [(g.db, g.car) for g in state.garages.values() if g.exists()]
        garages = lambda: list(state.garages.values())   # étalonnages, même d'une voiture encore jamais branchée
        threading.Thread(target=synchro.sync_loop, args=(state, bases, version(), garages), daemon=True).start()
    state.diag = diagnostic.Diag(state, args, data)   # onglet Diagnostic : scans rangés dans data/diag/
    state.set_settings({"max_hz": args.max_hz,
                        "delay": args.delay, "log_hz": args.log_hz, "fast": args.fast,
                        "demo": args.demo})
    threading.Thread(target=obd_loop, args=(state, args, logger_factory), daemon=True).start()

    try:
        server = QuietServer((args.bind, args.http), make_handler(state))
    except OSError as e:
        sys.exit(f"Erreur : impossible d'ouvrir le port {args.http} ({e.strerror}). Le dashboard tourne déjà"
                 f" (./service.sh status) ? Sinon changer PORT_PAGE dans car.conf.")
    print(f"Dashboard : http://{HOST}:{args.http}  (Ctrl+C pour quitter)")
    for n in lan_names() if on_lan else []:
        print(f"  sur le réseau : http://{n}:{args.http}")
    if on_lan:
        print("  (pas de mot de passe : tout le réseau peut voir le dashboard et changer réglages et notes)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
