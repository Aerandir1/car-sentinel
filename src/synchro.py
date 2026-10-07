"""Envoi de la base vers clio-sentinel-server, dès que le serveur est joignable.

Le serveur retient, pour chaque base (reconnue à l'identifiant de sa première session), la dernière mesure
reçue : chaque envoi reprend de là, par paquets, et un envoi coupé (réseau perdu en route) est simplement
refait au suivant. Les trajets et événements partent en bloc quand ils ont changé. Rien n'est jamais
modifié ni effacé dans la base locale.

    python src/synchro.py --server https://192.168.1.20:8090 --server-token JETON --server-pin EMPREINTE
"""

import argparse
import gzip
import hashlib
import http.client
import json
import os
import socket
import ssl
import time
import traceback
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import donnees
from etat import DERIVED, METRICS

BATCH = 5000          # mesures par envoi
EVERY_S = 60          # entre deux synchronisations
TIMEOUT_S = 15


class SyncError(Exception):
    pass


def meta():
    """Libellés et unités des métriques, pour que la page du serveur les affiche comme le dashboard."""
    out = [{"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold}
           for k, lbl, u, _cmd, _conv, rng, warn, cold in METRICS]
    return out + [{**DERIVED, "warn": None, "cold": None}]


class Client:
    """Connexion au serveur. Avec `pin`, le certificat est reconnu à son empreinte SHA-256 (auto-signé accepté) ;
    sinon il est vérifié normalement (certificat reconnu, ou `ca` : fichier de l'autorité, ex. rootCA.pem de mkcert)."""

    def __init__(self, url, token, pin=None, ca=None):
        u = urlsplit(url.rstrip("/"))
        if u.scheme not in ("http", "https") or not u.hostname:
            raise SyncError(f"adresse du serveur invalide : {url}")
        self.https, self.host, self.base = u.scheme == "https", u.hostname, u.path
        self.port = u.port or (443 if self.https else 80)
        self.token = token
        self.pin = pin.replace(":", "").lower() if pin else None
        if self.https:
            if self.pin:
                self.ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                self.ctx.check_hostname = False
                self.ctx.verify_mode = ssl.CERT_NONE   # vérifié juste après, à l'empreinte
            else:
                self.ctx = ssl.create_default_context(cafile=ca)

    def request(self, method, path, body=None, query=None):
        if self.https:
            conn = http.client.HTTPSConnection(self.host, self.port, timeout=TIMEOUT_S, context=self.ctx)
        else:
            conn = http.client.HTTPConnection(self.host, self.port, timeout=TIMEOUT_S)
        headers = {"Accept": "application/json"}
        if self.token:   # sans jeton : serveur lancé en --no-auth (essais)
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = gzip.compress(json.dumps(body, separators=(",", ":")).encode(), 6)
            headers.update({"Content-Type": "application/json", "Content-Encoding": "gzip"})
        try:
            conn.connect()
            if self.https and self.pin:
                got = hashlib.sha256(conn.sock.getpeercert(binary_form=True)).hexdigest()
                if got != self.pin:
                    raise SyncError(f"empreinte du certificat inattendue ({got}) : ce n'est pas le bon serveur,"
                                    " ou son certificat a été refait (redonner --server-pin)")
            conn.request(method, self.base + path + (f"?{urlencode(query)}" if query else ""), data, headers)
            r = conn.getresponse()
            payload = r.read()
        finally:
            conn.close()
        if r.status == 401:
            raise SyncError("jeton refusé par le serveur (--server-token)")
        try:
            return r.status, json.loads(payload) if payload else None
        except ValueError:
            raise SyncError(f"réponse inattendue du serveur ({r.status}) : {payload[:200]!r}")


def fingerprint(db):
    """Identifiant de la base : celui de sa première session (None tant qu'il n'y en a pas)."""
    row = db.execute("SELECT id FROM sessions ORDER BY start_ms, rowid LIMIT 1").fetchone()
    return row[0] if row else None


def trips_payload(db):
    trips = [dict(zip(donnees.TRIP_FIELDS, r)) for r in db.execute(
        f"SELECT {', '.join(donnees.TRIP_FIELDS)} FROM trips ORDER BY start_ms, id")]
    events = [list(r) for r in db.execute("SELECT t_ms, kind, key, value, detail FROM events ORDER BY t_ms, rowid")]
    h = hashlib.sha256(json.dumps([trips, events], separators=(",", ":")).encode()).hexdigest()
    return trips, events, h


def sync_once(db_path, client, name=None, version=None):
    """Envoie tout ce que le serveur n'a pas encore. Renvoie le nombre de mesures envoyées."""
    db = donnees.connect(db_path)
    try:
        origin = fingerprint(db)
        if origin is None:
            return 0
        code, st = client.request("GET", "/api/sync", query={"origin": origin})
        if code != 200:
            raise SyncError(f"le serveur a répondu {code} : {st}")
        cursor, sent = st["last_rowid"], 0
        while True:
            rows = db.execute("SELECT rowid, session_id, t_ms, key, value FROM samples WHERE rowid > ?"
                              " ORDER BY rowid LIMIT ?", (cursor, BATCH)).fetchall()
            body = {"origin": origin, "name": name, "version": version, "after": cursor,
                    "last": rows[-1][0] if rows else cursor, "samples": [list(r[1:]) for r in rows]}
            sids = sorted({r[1] for r in rows})
            body["sessions"] = [list(s) for s in db.execute(
                f"SELECT id, start_ms, end_ms, source, vin, version, metrics FROM sessions"
                f" WHERE id IN ({','.join('?' * len(sids))})", sids)] if sids else []
            if not sent:
                body["meta"] = meta()
            if len(rows) < BATCH:   # dernier paquet : les trajets partent avec, s'ils ont changé
                trips, events, h = trips_payload(db)
                if h != st.get("trips_hash"):
                    body.update(trips=trips, events=events, trips_hash=h)
                elif not rows:
                    return sent
            code, st = client.request("POST", "/api/sync", body)
            if code == 409:   # le serveur n'en est pas là (base du serveur restaurée…) : on repart de son curseur
                cursor = st["last_rowid"]
                continue
            if code != 200:
                raise SyncError(f"le serveur a refusé l'envoi ({code}) : {st}")
            cursor, sent = st["last_rowid"], sent + len(rows)
            if len(rows) < BATCH:
                return sent
    finally:
        db.close()


def sync_loop(state, db_path, client, name, version):
    """Thread de fond : synchronise toutes les EVERY_S secondes ; hors réseau, réessaie sans bruit."""
    was_ok = None
    while True:
        try:
            n = sync_once(db_path, client, name, version)
            state.sync = {"ok": True, "t": time.time(), "sent": n}
            if n or was_ok is not True:
                print(f"Synchro serveur : {n} mesure{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}" if n
                      else "Synchro serveur : à jour")
            was_ok = True
        except (OSError, socket.timeout, http.client.HTTPException, SyncError) as e:
            state.sync = {"ok": False, "t": time.time(), "error": str(e)}
            if was_ok is not False:   # n'annonce que le passage de joignable à injoignable
                print(f"Synchro serveur impossible ({e}), nouvel essai toutes les {EVERY_S} s")
            was_ok = False
        except Exception:
            traceback.print_exc()
            was_ok = False
        time.sleep(EVERY_S)


def add_arguments(p):
    p.add_argument("--server", metavar="URL", default=os.environ.get("CLIO_SERVER"),
                   help="clio-sentinel-server où envoyer la base, ex. https://192.168.1.20:8090 (ou CLIO_SERVER)")
    p.add_argument("--server-token", default=os.environ.get("CLIO_SERVER_TOKEN"),
                   help="jeton du serveur (ou CLIO_SERVER_TOKEN) ; à omettre si le serveur est en --no-auth")
    p.add_argument("--server-pin", metavar="SHA256", default=os.environ.get("CLIO_SERVER_PIN"),
                   help="empreinte du certificat du serveur, affichée à son lancement (ou CLIO_SERVER_PIN)")
    p.add_argument("--server-ca", metavar="FICHIER.pem", help="autorité qui a signé le certificat du serveur")
    p.add_argument("--server-name", default=os.environ.get("CLIO_SERVER_NAME") or socket.gethostname(),
                   help="nom de ce PC sur la page du serveur (défaut : nom de la machine)")


def make_client(args):
    return Client(args.server, args.server_token, args.server_pin, args.server_ca)


def main():
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", default=here / "data" / "clio.db", help="base SQLite (défaut data/clio.db)")
    add_arguments(p)
    args = p.parse_args()
    if not args.server:
        p.error("--server manquant")
    from dashboard_clio import version
    try:
        n = sync_once(args.db, make_client(args), args.server_name, version())
    except (OSError, http.client.HTTPException, SyncError) as e:
        raise SystemExit(f"Synchro impossible : {e}")
    print(f"{n} mesure{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}, serveur à jour")


if __name__ == "__main__":
    main()
