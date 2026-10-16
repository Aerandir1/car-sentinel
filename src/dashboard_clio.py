#!/usr/bin/env python3
"""Dashboard OBD-II temps réel pour Clio 4 (ELM327 + python-obd).

Lit l'ECU en boucle dans un thread et sert dashboard.html + une API JSON
sur http://clio-sentinel.localhost:8080. Aucune dépendance web : tout marche hors ligne.
Réglages dans clio.conf (voir clio.conf.exemple) ; les options ci-dessous passent devant.

    python src/dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv     (Linux)
    python src/dashboard_clio.py --port COM3 --log trajet.csv             (Windows)
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
import donnees
import etalonnage
import synchro
import trajets
from acquisition import obd_loop
from reseau import HOST, lan_names
from etat import CsvLogger, State
from serveur import QuietServer, make_handler

HERE = Path(__file__).resolve().parent.parent
DATA = HERE / "data"   # bases SQLite des mesures horodatées (voir donnees.py)
TRIPS_EVERY_S = 10     # mise à jour des trajets


def version():
    """Commit git du script, enregistré avec chaque session (CLIO_VERSION sans dossier .git)."""
    try:
        out = subprocess.run(["git", "-C", str(HERE), "describe", "--always", "--dirty"],
                             capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return os.environ.get("CLIO_VERSION")


def trips_loop(state, db_path, demo_path):
    """Découpe les mesures en trajets au fil de l'eau (voir trajets.py), dans la vraie base et celle de la démo."""
    while True:
        try:
            trip = trajets.update(db_path)
            demo_trip = trajets.update(demo_path)
            state.trip = demo_trip if state.get_settings().get("demo") else trip
        except Exception:
            traceback.print_exc()
        time.sleep(TRIPS_EVERY_S)


def main():
    if sys.stdout is None:
        # Lancé sans console (pythonw, service Windows : service.ps1) : le journal va dans logs/clio.log
        (HERE / "logs").mkdir(exist_ok=True)
        sys.stdout = sys.stderr = open(HERE / "logs" / "clio.log", "a", encoding="utf-8", buffering=1)
        print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="port série de l'ELM327 : /dev/ttyUSB0, COM3… (auto-détection si absent)")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    p.add_argument("--db", metavar="FICHIER.db",
                   help="base SQLite des mesures horodatées (défaut data/clio.db)")
    p.add_argument("--no-db", action="store_true", help="n'enregistre pas les mesures")
    p.add_argument("--log", metavar="FICHIER.csv", help="enregistre les mesures en CSV")
    p.add_argument("--log-hz", type=float, default=5.0, help="fréquence du CSV (défaut 5 Hz)")
    p.add_argument("--delay", type=float, default=0.0, help="pause en s entre deux requêtes OBD")
    p.add_argument("--max-hz", type=float, default=20.0, help="fréquence max d'acquisition (défaut 20)")
    p.add_argument("--fast", action="store_true", help="mode rapide python-obd (requêtes plus courtes)")
    p.add_argument("--demo", action="store_true", help="démarre en mode démo (trajet simulé, base à part)")
    p.add_argument("--http", type=int, default=8080, help="port local de la page web (défaut 8080)")
    p.add_argument("--bind", default="127.0.0.1",
                   help="adresse d'écoute (défaut 127.0.0.1)")
    p.add_argument("--lan", action="store_true",
                   help="page accessible depuis les autres appareils du réseau (= --bind 0.0.0.0)")
    synchro.add_arguments(p)
    args = p.parse_args(config.argv() + sys.argv[1:])
    if args.lan:
        args.bind = "0.0.0.0"
    on_lan = args.lan or args.bind not in ("127.0.0.1", "localhost", "::1")
    args.dev = "/dev"   # où la détection cherche l'ELM327 sous Linux (Windows : ports COM, acquisition.py)
    # Une synchro mal réglée ne doit pas empêcher le dashboard de tourner : on prévient et on continue sans
    client = None
    if args.server and args.no_db:
        print("Attention : --server envoie la base, incompatible avec --no-db : synchro désactivée")
    elif args.server:
        try:
            client = synchro.make_client(args)
        except synchro.SyncError as e:
            print(f"Attention : {e} : synchro désactivée (SERVEUR dans clio.conf)")

    def logger_factory(keys):
        return CsvLogger(args.log, keys) if args.log else None

    state = State()
    db_path = demo_path = None
    if not args.no_db:
        db_path = Path(args.db) if args.db else DATA / "clio.db"
        demo_path = db_path.with_name(db_path.stem + "-demo.db")   # mode démo : base à part, jamais mêlée
        db_path.parent.mkdir(parents=True, exist_ok=True)
        state.recorder = donnees.Recorder(db_path, version())
        state.demo_recorder = donnees.Recorder(demo_path, version())
        threading.Thread(target=trips_loop, args=(state, db_path, demo_path), daemon=True).start()
        print(f"Mesures enregistrées dans {db_path} (mode démo : {demo_path.name})")
        if client:
            state.sync = {"ok": None, "t": time.time()}   # premier essai en cours (témoin « serveur » du dashboard)
            bases = [(db_path, args.server_name), (demo_path, f"{args.server_name} · démo")]
            threading.Thread(target=synchro.sync_loop, args=(state, bases, client, version()), daemon=True).start()
            print(f"Synchro vers {client.url} (toutes les {synchro.EVERY_S} s quand il est joignable)")
    # Étalonnage des mesures Renault : à côté de la base (data/ par défaut)
    calib_dir = db_path.parent if db_path else DATA
    calib_dir.mkdir(parents=True, exist_ok=True)
    state.calib = etalonnage.Store(calib_dir / "etalonnage.json")
    state.diag = diagnostic.Diag(state, args, calib_dir)   # onglet Diagnostic : scans rangés dans data/diag/
    state.set_settings({"max_hz": args.max_hz,
                        "delay": args.delay, "log_hz": args.log_hz, "fast": args.fast,
                        "demo": args.demo})
    threading.Thread(target=obd_loop, args=(state, args, logger_factory), daemon=True).start()

    try:
        server = QuietServer((args.bind, args.http), make_handler(
            state, lambda: demo_path if state.get_settings().get("demo") else db_path))
    except OSError as e:
        sys.exit(f"Erreur : impossible d'ouvrir le port {args.http} ({e.strerror}). Le dashboard tourne déjà"
                 f" (./service.sh status) ? Sinon changer PORT_PAGE dans clio.conf.")
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
