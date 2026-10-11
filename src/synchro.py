"""Envoi de la base vers clio-sentinel-server, dès que le serveur est joignable.

Le serveur retient, pour chaque base (reconnue à l'identifiant de sa première session), la dernière mesure
reçue : chaque envoi reprend de là, par paquets, et un envoi coupé (réseau perdu en route) est simplement
refait au suivant. Les trajets et événements partent en bloc quand ils ont changé. Rien n'est jamais
modifié ni effacé dans la base locale.

    python src/synchro.py --server http://192.168.1.13:8207,http://10.100.0.1:8207

Plusieurs adresses (séparées par des virgules) : la première qui répond est prise, et essayée en premier la
fois suivante. Ex. la maison en direct sur le Wi-Fi, puis le VPN WireGuard sur la route. HTTP simple, sans jeton :
le serveur n'est joignable que depuis la maison et le VPN.
"""

import argparse
import gzip
import hashlib
import http.client
import json
import os
import socket
import time
import traceback
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import donnees
from etat import DERIVED, METRICS

BATCH = 5000          # mesures par envoi
EVERY_S = 60          # entre deux synchronisations
PING_S = 3            # entre deux présences envoyées au serveur (témoins du dashboard et du serveur)
TIMEOUT_S = 15
CONNECT_S = 3         # une adresse qui ne répond pas est vite abandonnée pour la suivante
PING_TIMEOUT_S = 2    # vérification : connexion et réponse
DEFAULT_PORT = 8207   # port de clio-sentinel-server


class SyncError(Exception):
    pass


def meta():
    """Libellés et unités des métriques, pour que la page du serveur les affiche comme le dashboard."""
    out = [{"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold}
           for k, lbl, u, _cmd, _conv, rng, warn, cold in METRICS]
    return out + [{**DERIVED, "warn": None, "cold": None}]


class Client:
    """Connexion au serveur, à la première de ses adresses qui répond."""

    def __init__(self, urls):
        self.addrs = []
        for url in filter(None, (u.strip().rstrip("/") for u in urls.split(","))):
            # « 10.100.0.1 » ou « 10.100.0.1:8207 » suffisent : http:// et le port par défaut sont ajoutés
            try:
                u = urlsplit(url if "://" in url else "http://" + url)
                addr = (u.hostname, u.port or DEFAULT_PORT, u.path)
            except ValueError:
                u = None
            if not u or u.scheme != "http" or not u.hostname:
                raise SyncError(f"adresse du serveur invalide : {url} (attendu IP, IP:PORT ou http://IP:PORT)")
            self.addrs.append(addr)
        if not self.addrs:
            raise SyncError("aucune adresse de serveur")
        self.current = self.addrs[0]

    def _connect(self, connect_s=CONNECT_S, timeout_s=TIMEOUT_S):
        """Ouvre la connexion : l'adresse qui a marché la dernière fois d'abord, puis les autres."""
        errors = []
        for host, port, base in [self.current] + [a for a in self.addrs if a != self.current]:
            conn = http.client.HTTPConnection(host, port, timeout=connect_s)
            try:
                conn.connect()
            except OSError as e:
                conn.close()
                errors.append(f"{host}:{port} {e}")
                continue
            conn.sock.settimeout(timeout_s)
            self.current = (host, port, base)
            return conn, base
        raise SyncError("serveur injoignable (" + " ; ".join(errors) + ")")

    @property
    def url(self):
        host, port, base = self.current
        return f"http://{host}:{port}{base}"

    def request(self, method, path, body=None, query=None, timeout_s=None):
        conn, base = self._connect(*(timeout_s, timeout_s) if timeout_s else ())
        # Adresse de ce PC sur la route prise (Wi-Fi ou VPN) : le serveur derrière Docker ne la voit pas toujours
        headers = {"Accept": "application/json", "X-Clio-Local-IP": conn.sock.getsockname()[0]}
        data = None
        if body is not None:
            data = gzip.compress(json.dumps(body, separators=(",", ":")).encode(), 6)
            headers.update({"Content-Type": "application/json", "Content-Encoding": "gzip"})
        try:
            conn.request(method, base + path + (f"?{urlencode(query)}" if query else ""), data, headers)
            r = conn.getresponse()
            payload = r.read()
        finally:
            conn.close()
        try:
            return r.status, json.loads(payload) if payload else None
        except ValueError:
            if r.status >= 400:   # erreur en texte brut (« not found »…) : c'est l'appelant qui juge du code
                return r.status, payload[:200].decode(errors="replace")
            raise SyncError(f"réponse inattendue du serveur ({r.status}) : {payload[:200]!r}")


def ping(client):
    """Le serveur répond-il ? Requête légère, délais courts."""
    code, _ = client.request("GET", "/api/sync", query={"origin": ""}, timeout_s=PING_TIMEOUT_S)
    if code != 200:
        raise SyncError(f"le serveur a répondu {code}")


def hello(client, state, name, version):
    """Présence : ce PC, son adresse et l'état de l'ELM327, affichés sur la page du serveur.
    Serveur d'avant /api/hello (404) : simple vérification qu'il répond."""
    body = {"name": name, "host": socket.gethostname(), "version": version,
            "obd": {"adapter": state.adapter, "status": state.status, "source": state.source, "vin": state.vin}}
    code, _ = client.request("POST", "/api/hello", body, timeout_s=PING_TIMEOUT_S)
    if code == 404:
        ping(client)
    elif code != 200:
        raise SyncError(f"le serveur a répondu {code}")


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
        if origin is None:   # rien à envoyer : on vérifie quand même que le serveur répond (témoin du dashboard)
            ping(client)
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


def sync_loop(state, bases, client, version):
    """Thread de fond : envoie sa présence toutes les PING_S secondes (le serveur répond-il ?) et synchronise
    toutes les EVERY_S secondes, ou dès qu'il revient. Hors réseau, réessaie sans bruit.

    state.sync (témoin du dashboard) : ok, t (dernier contact ou essai), url, error, et du dernier envoi
    réussi : synced (heure) et sent (mesures envoyées)."""
    was_ok, next_sync, last = None, 0.0, {}
    while True:
        try:
            hello(client, state, bases[0][1], version)   # présence sous le nom de la vraie base
            if time.time() >= next_sync:
                # Chaque base est une origine à part sur le serveur (la démo n'y est pas mêlée aux vrais trajets)
                n = sum(sync_once(db_path, client, name, version) for db_path, name in bases)
                next_sync = time.time() + EVERY_S
                last = {"synced": time.time(), "sent": n}
                if n or was_ok is not True:
                    print(f"Synchro serveur ({client.url}) : {n} mesure{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}"
                          if n else f"Synchro serveur ({client.url}) : à jour")
            state.sync = {"ok": True, "t": time.time(), "url": client.url, **last}
            was_ok = True
        except (OSError, socket.timeout, http.client.HTTPException, SyncError) as e:
            state.sync = {"ok": False, "t": time.time(), "error": str(e), **last}
            if was_ok is not False:   # n'annonce que le passage de joignable à injoignable
                print(f"Synchro serveur impossible ({e}), nouvel essai toutes les {PING_S} s")
            was_ok, next_sync = False, 0.0   # au retour du serveur, on synchronise tout de suite
        except Exception:
            traceback.print_exc()
            state.sync = {"ok": False, "t": time.time(), "error": "erreur interne de la synchro (voir le journal)", **last}
            was_ok, next_sync = False, time.time() + EVERY_S
        time.sleep(PING_S)


def add_arguments(p):
    p.add_argument("--server", metavar="URL[,URL…]", default=os.environ.get("CLIO_SERVER"),
                   help="clio-sentinel-server où envoyer la base, une ou plusieurs adresses séparées par des virgules,"
                        " ex. http://192.168.1.13:8207,http://10.100.0.1:8207 (ou CLIO_SERVER)")
    p.add_argument("--server-name", default=os.environ.get("CLIO_SERVER_NAME") or socket.gethostname(),
                   help="nom de ce PC sur la page du serveur (défaut : nom de la machine)")


def make_client(args):
    return Client(args.server)


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
        client = make_client(args)
        n = sync_once(args.db, client, args.server_name, version())
    except (OSError, http.client.HTTPException, SyncError) as e:
        raise SystemExit(f"Synchro impossible : {e}")
    print(f"{n} mesure{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}, serveur à jour ({client.url})")


if __name__ == "__main__":
    main()
