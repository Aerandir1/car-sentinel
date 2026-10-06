#!/usr/bin/env python3
"""Dashboard OBD-II temps réel pour Clio 4 (ELM327 + python-obd).

Lit l'ECU en boucle dans un thread et sert dashboard.html + une API JSON
sur https://127.0.0.1:8080. Aucune dépendance web : tout marche hors ligne.

    python dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv
"""

import argparse
import ssl
import threading
from pathlib import Path

import donnees
from acquisition import obd_loop
from certs import ensure_cert
from etat import CsvLogger, State
from serveur import QuietServer, make_handler

DATA = Path(__file__).resolve().parent / "data"   # bases SQLite des mesures horodatées (voir donnees.py)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="port série de l'ELM327 (auto-détection si absent)")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    p.add_argument("--db", metavar="FICHIER.db",
                   help="base SQLite des mesures horodatées (défaut data/clio.db)")
    p.add_argument("--no-db", action="store_true", help="n'enregistre pas les mesures")
    p.add_argument("--log", metavar="FICHIER.csv", help="enregistre les mesures en CSV")
    p.add_argument("--log-hz", type=float, default=5.0, help="fréquence du CSV (défaut 5 Hz)")
    p.add_argument("--delay", type=float, default=0.0, help="pause en s entre deux requêtes OBD")
    p.add_argument("--max-hz", type=float, default=20.0, help="fréquence max d'acquisition (défaut 20)")
    p.add_argument("--fast", action="store_true", help="mode rapide python-obd (requêtes plus courtes)")
    p.add_argument("--http", type=int, default=8080, help="port local de la page web (défaut 8080)")
    p.add_argument("--no-https", action="store_true", help="sert la page en HTTP simple (sans certificat)")
    p.add_argument("--bind", default="127.0.0.1",
                   help="adresse d'écoute (défaut 127.0.0.1 ; 0.0.0.0 dans un conteneur Docker)")
    args = p.parse_args()

    def logger_factory(keys):
        return CsvLogger(args.log, keys) if args.log else None

    state = State()
    db_path = None
    if not args.no_db:
        db_path = Path(args.db) if args.db else DATA / "clio.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        state.recorder = donnees.Recorder(db_path)
        print(f"Mesures enregistrées dans {db_path}")
    state.set_settings({"max_hz": args.max_hz,
                        "delay": args.delay, "log_hz": args.log_hz, "fast": args.fast})
    threading.Thread(target=obd_loop, args=(state, args, logger_factory), daemon=True).start()

    scheme = "http" if args.no_https else "https"
    server = QuietServer((args.bind, args.http), make_handler(state, scheme, db_path))
    if not args.no_https:
        crt, key, trusted = ensure_cert()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        if not trusted:
            print("Certificat auto-signé : le navigateur affichera un avertissement à accepter une fois.\n"
                  "Pour l'éviter : sudo pacman -S mkcert nss && mkcert -install, puis relancer le script.")
    print(f"Dashboard : {scheme}://127.0.0.1:{args.http}  (Ctrl+C pour quitter)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nArrêt.")


if __name__ == "__main__":
    main()
