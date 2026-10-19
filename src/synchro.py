"""Envoi des bases vers clio-sentinel-server, dès que le serveur est joignable.

Une base par voiture (data/clio.db, data/fiesta.db), chacune envoyée comme une origine à part, avec sa voiture
déclarée (« clio », « fiesta » : le serveur vérifie avec le VIN). Le serveur retient, pour chaque base (reconnue à
l'identifiant de sa première session), la dernière mesure reçue : chaque envoi reprend de là, par paquets, et un
envoi coupé (réseau perdu en route) est simplement refait au suivant. Les trajets et événements partent en bloc quand ils ont changé. Rien n'est jamais
modifié ni effacé dans la base locale.

Vérification (au lancement puis toutes les VERIFY_S secondes) : les mesures déjà envoyées sont comparées par
empreinte, session par session et métrique par métrique ; un groupe corrigé après coup dans la base locale
(échelle d'une mesure rectifiée, par ex.) est renvoyé et remplace celui du serveur.

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
import voitures
from etat import derived_meta

BATCH = 5000          # mesures par envoi
EVERY_S = 60          # entre deux synchronisations
VERIFY_S = 3600       # entre deux vérifications des mesures déjà envoyées
PING_S = 3            # entre deux présences envoyées au serveur (témoins du dashboard et du serveur)
TIMEOUT_S = 15
CONNECT_S = 3         # une adresse qui ne répond pas est vite abandonnée pour la suivante
PING_TIMEOUT_S = 2    # vérification : connexion et réponse
DEFAULT_PORT = 8207   # port de clio-sentinel-server


class SyncError(Exception):
    pass


def meta(car):
    """Libellés et unités des métriques de la voiture, pour que la page du serveur les affiche comme le dashboard."""
    out = [{"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold}
           for k, lbl, u, _cmd, _conv, rng, warn, cold in car.METRICS]
    return out + [derived_meta(d) for d in car.DERIVED]


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
    body = {"name": name, "host": socket.gethostname(), "version": version, "vehicle": state.car.ID,
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


def sync_once(db_path, client, name=None, version=None, car=None):
    """Envoie tout ce que le serveur n'a pas encore de la base de cette voiture. Renvoie le nombre de mesures
    envoyées."""
    car = car or voitures.get(None)
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
            body = {"origin": origin, "name": name, "version": version, "vehicle": car.ID, "after": cursor,
                    "last": rows[-1][0] if rows else cursor, "samples": [list(r[1:]) for r in rows]}
            sids = sorted({r[1] for r in rows})
            body["sessions"] = [list(s) for s in db.execute(
                f"SELECT id, start_ms, end_ms, source, vin, version, metrics FROM sessions"
                f" WHERE id IN ({','.join('?' * len(sids))})", sids)] if sids else []
            if not sent:
                body["meta"] = meta(car)
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


DIAG_SENT = "envoyes.txt"   # dans data/diag : empreinte des diagnostics déjà reçus par le serveur, une par ligne


def sync_diag(client, diag_dir, name=None):
    """Diagnostics (data/diag/scan-*.json, dtc-*.json) pas encore reçus par le serveur : envoyés un par un, le
    serveur les range par VIN. Un fichier modifié repart (autre empreinte). Renvoie le nombre envoyé ; 0 avec un
    serveur d'avant les diagnostics (404)."""
    from pathlib import Path
    diag_dir = Path(diag_dir)
    if not diag_dir.is_dir():
        return 0
    sent_file = diag_dir / DIAG_SENT
    try:
        sent = set(sent_file.read_text(encoding="utf-8").split())
    except OSError:
        sent = set()
    n = 0
    for f in sorted(diag_dir.glob("*.json")):
        raw = f.read_bytes()
        h = hashlib.sha256(raw).hexdigest()
        if h in sent:
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        code, r = client.request("POST", "/api/diagnostics", {"name": f.name, "source": name, "data": data})
        if code == 404:
            return n
        if code != 200:
            raise SyncError(f"le serveur a refusé le diagnostic {f.name} ({code}) : {r}")
        sent.add(h)
        with open(sent_file, "a", encoding="utf-8") as out:
            out.write(h + "\n")
        n += 1
    return n


def sync_calib(client, garage, name=None):
    """Étalonnage de la voiture : celui du serveur fusionné ici (mesure par mesure, la plus récente gagne), puis le
    résultat renvoyé s'il diffère du sien. Un nouveau client récupère ainsi tout l'étalonnage. Renvoie
    (mesures reprises du serveur, envoyé ?) ; (0, False) avec un serveur d'avant l'étalonnage (404)."""
    car, store = garage.car, garage.calib
    code, st = client.request("GET", "/api/calib", query={"vehicle": car.ID})
    if code == 404:
        return 0, False
    if code != 200:
        raise SyncError(f"le serveur a répondu {code} : {st}")
    remote = (st or {}).get("data") or {}
    before = store.snapshot()
    taken = sum(1 for k in remote if remote[k] != before.get(k)) if store.merge(remote) else 0
    mine = store.snapshot()
    if not mine or mine == remote:
        return taken, False
    code, st = client.request("POST", "/api/calib", {"vehicle": car.ID, "data": mine, "source": name})
    if code != 200:
        raise SyncError(f"le serveur a refusé l'étalonnage ({code}) : {st}")
    return taken, True


def sync_prices(client, state):
    """Prix du carburant de chaque voiture, avec ceux du serveur (table prices : par voiture, à partir d'une date,
    réglés sur sa page ou envoyés par un dashboard). Un prix réglé ici part au serveur, daté du moment du réglage ;
    un prix plus récent sur le serveur (autre client, page du serveur) remplace celui d'ici. Renvoie la liste des
    changements, pour le journal ; [] avec un serveur sans prix (404)."""
    code, st = client.request("GET", "/api/prices")
    if code == 404:
        return []
    if code != 200:
        raise SyncError(f"le serveur a répondu {code} : {st}")
    now, out = int(time.time() * 1000), []
    for car_id, (price, sync) in state.price_sync().items():
        prices = ((st or {}).get(car_id) or {}).get("prices", [])
        # Prix d'avant la synchro des prix (sans instant) : il part si le serveur n'en a aucun pour cette voiture
        if price is not None and not sync and not prices:
            sync = {"t": now, "pending": True}
        if price is not None and sync.get("pending"):
            if price <= 0:   # 0 : pas de prix, rien à envoyer
                state.price_synced(car_id)
                continue
            _post_price(client, car_id, price, sync.get("t") or now, sync.get("source") or state.name())
            state.price_synced(car_id, t=sync.get("t") or now, source=sync.get("source") or state.name())
            out.append(f"{car_id} : {price:.3f} €/L envoyé au serveur")
            continue
        # Prix du serveur : le dernier réglé parmi ceux déjà commencés (un prix réglé sur la page « à partir
        # d'aujourd'hui » passe devant un prix envoyé plus tôt dans la journée), s'il a été réglé après celui d'ici
        started = [p for p in prices if p["from_ms"] <= now]
        current = max(started, key=lambda p: p.get("set_ms") or p["from_ms"], default=None)
        set_ms = current and (current.get("set_ms") or current["from_ms"])
        if current and set_ms > (sync.get("t") or 0) and current["price"] != price:
            source = current.get("source") or "page du serveur"
            state.price_synced(car_id, current["price"], set_ms, source)
            out.append(f"{car_id} : {current['price']:.3f} €/L repris du serveur (réglé par {source})")
        elif current and current["price"] == price and not sync.get("source"):
            state.price_synced(car_id, t=set_ms, source=current.get("source") or "page du serveur")   # origine connue
    return out


def _post_price(client, car_id, price, t, source):
    code, r = client.request("POST", "/api/prices", {"vehicle": car_id, "price": price, "from_ms": t, "source": source})
    if code != 200:
        raise SyncError(f"le serveur a refusé le prix ({code}) : {r}")


def push_price(state, car_id):
    """Prix d'une voiture validé dans le dashboard : envoyé tout de suite au serveur, qui fait foi ensuite pour
    tous les dashboards. Renvoie (envoyé ?, raison si non) ; non envoyé, il reste à envoyer (sync_prices)."""
    urls = state.get_settings().get("server") or ""
    if not urls:
        return False, "pas de serveur réglé (Réglages → Serveur)"
    price, sync = state.price_sync()[car_id]
    if price is None or not sync.get("pending"):
        return True, None
    try:
        _post_price(Client(urls), car_id, price, sync.get("t"), sync.get("source") or state.name())
    except (OSError, socket.timeout, http.client.HTTPException, SyncError) as e:
        return False, str(e)
    state.price_synced(car_id)
    return True, None


def digest_rows(rows):
    """Empreinte de chaque groupe (session, métrique) de lignes (session_id, key, t_ms, value) triées dans cet ordre.
    Même calcul que dans clio-sentinel-server (base.py) : les deux doivent rester identiques."""
    out, cur = {}, None
    for sid, key, t, v in rows:
        if (sid, key) != cur:
            cur = (sid, key)
            h = out[f"{sid}|{key}"] = hashlib.sha256()
        h.update(f"{t}:{v!r};".encode())
    return {k: h.hexdigest()[:16] for k, h in out.items()}


def verify_once(db_path, client):
    """Renvoie les groupes (session, métrique) déjà envoyés mais modifiés depuis dans la base locale.
    Renvoie le nombre de groupes corrigés sur le serveur (0 avec un serveur d'avant la vérification)."""
    db = donnees.connect(db_path)
    try:
        origin = fingerprint(db)
        if origin is None:
            return 0
        code, st = client.request("GET", "/api/sync/digest", query={"origin": origin})
        if code == 404:
            return 0
        if code != 200:
            raise SyncError(f"le serveur a répondu {code} : {st}")
        # Seulement ce que le serveur a reçu : les mesures après son curseur partiront avec la synchro normale
        cursor = st["last_rowid"]
        mine = digest_rows(db.execute("SELECT session_id, key, t_ms, value FROM samples WHERE rowid <= ?"
                                      " ORDER BY 1, 2, 3, 4", (cursor,)))
        theirs, fixed = st["digests"], 0
        for k in sorted(set(mine) | set(theirs)):
            if mine.get(k) == theirs.get(k):
                continue
            sid, key = k.split("|", 1)
            rows = db.execute("SELECT t_ms, value FROM samples WHERE rowid <= ? AND session_id = ? AND key = ?"
                              " ORDER BY t_ms, value", (cursor, sid, key)).fetchall()
            code, r = client.request("POST", "/api/sync/fix", {"origin": origin, "after": cursor, "session_id": sid,
                                                               "key": key, "samples": [list(x) for x in rows]})
            if code == 409:   # le serveur a reçu d'autres mesures entre-temps : reprise à la prochaine vérification
                break
            if code != 200:
                raise SyncError(f"le serveur a refusé la correction ({code}) : {r}")
            fixed += 1
        return fixed
    finally:
        db.close()


def sync_loop(state, bases, version, garages=lambda: []):
    """Thread de fond : envoie sa présence toutes les PING_S secondes (le serveur répond-il ?) et synchronise
    toutes les EVERY_S secondes, ou dès qu'il revient. Hors réseau, réessaie sans bruit.

    Adresse et nom sont relus à chaque tour dans les réglages (server, server_name : onglet Réglages, ou
    SERVEUR et NOM_PC de car.conf) : un changement est pris tout de suite, une adresse vide arrête l'envoi.
    bases() : [(base, voiture)] des voitures qui ont une base. La base de la démo n'est jamais envoyée.
    garages() : les voitures (etat.Garage) dont l'étalonnage s'échange avec le serveur (sync_calib).

    state.sync (témoin du dashboard) : None sans serveur ; sinon ok, t (dernier contact ou essai), url, error,
    et du dernier envoi réussi : synced (heure) et sent (mesures envoyées)."""
    client, conf = None, None
    while True:
        settings = state.get_settings()
        urls, name = settings.get("server") or "", settings.get("server_name") or socket.gethostname()
        if (urls, name) != conf:
            conf, client = (urls, name), None
            was_ok, next_sync, next_verify, last = None, 0.0, 0.0, {}
            state.sync = None
            if urls:
                try:
                    client = Client(urls)
                    state.sync = {"ok": None, "t": time.time()}   # premier essai en cours
                    print(f"Synchro vers {client.url} (toutes les {EVERY_S} s quand il est joignable)")
                except SyncError as e:
                    state.sync = {"ok": False, "t": time.time(), "error": str(e)}
                    print(f"Attention : {e} : synchro désactivée")
        if client is None:
            time.sleep(PING_S)
            continue
        try:
            hello(client, state, name, version)
            if time.time() < next_sync and any(s.get("pending") for _p, s in state.price_sync().values()):
                # Prix validé pendant que le serveur ne répondait pas : envoyé dès qu'il répond, sans attendre le tour
                for line in sync_prices(client, state):
                    print(f"Prix du carburant, {line}")
            if time.time() >= next_sync:
                n = sum(sync_once(db, client, name, version, car) for db, car in bases())
                for line in sync_prices(client, state):
                    print(f"Prix du carburant, {line}")
                if state.diag is not None:
                    nd = sync_diag(client, state.diag.dir, name)
                    if nd:
                        print(f"Diagnostics : {nd} envoyé{'s' if nd > 1 else ''} au serveur")
                for g in garages():
                    taken, sent = sync_calib(client, g, name)
                    if taken:
                        print(f"Étalonnage {g.car.NAME} : {taken} mesure{'s' if taken > 1 else ''} reprise"
                              f"{'s' if taken > 1 else ''} du serveur")
                        if g.car is state.car:
                            state.calib_changed()   # tuiles validées depuis un autre client : la page recharge
                    elif sent:
                        print(f"Étalonnage {g.car.NAME} envoyé au serveur")
                next_sync = time.time() + EVERY_S
                last = {"synced": time.time(), "sent": n}
                if n or was_ok is not True:
                    print(f"Synchro serveur ({client.url}) : {n} mesure{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}"
                          if n else f"Synchro serveur ({client.url}) : à jour")
                if time.time() >= next_verify:
                    f = sum(verify_once(db, client) for db, _car in bases())
                    next_verify = time.time() + VERIFY_S
                    if f:
                        print(f"Synchro serveur : {f} groupe{'s' if f > 1 else ''} de mesures corrigé{'s' if f > 1 else ''}")
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
    p.add_argument("--server", metavar="URL[,URL…]", default=os.environ.get("CAR_SERVER"),
                   help="clio-sentinel-server où envoyer les bases, une ou plusieurs adresses séparées par des virgules,"
                        " ex. http://192.168.1.13:8207,http://10.100.0.1:8207 (ou CAR_SERVER)")
    p.add_argument("--server-name", default=os.environ.get("CAR_SERVER_NAME") or socket.gethostname(),
                   help="nom de ce PC sur la page du serveur (défaut : nom de la machine)")


def make_client(args):
    return Client(args.server)


def main():
    here = Path(__file__).resolve().parent.parent
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--voiture", choices=sorted(voitures.CARS), default=voitures.DEFAULT,
                   help=f"voiture dont on envoie la base (défaut {voitures.DEFAULT})")
    p.add_argument("--db", help="base SQLite (défaut data/<voiture>.db)")
    add_arguments(p)
    args = p.parse_args()
    if not args.server:
        p.error("--server manquant")
    car = voitures.get(args.voiture)
    db = args.db or here / "data" / f"{car.ID}.db"
    from car_sentinel import version
    try:
        client = make_client(args)
        n = sync_once(db, client, args.server_name, version(), car)
        f = verify_once(db, client)
    except (OSError, http.client.HTTPException, SyncError) as e:
        raise SystemExit(f"Synchro impossible : {e}")
    print(f"{n} mesure{'s' if n > 1 else ''} envoyée{'s' if n > 1 else ''}, {f} groupe{'s' if f > 1 else ''}"
          f" de mesures corrigé{'s' if f > 1 else ''}, serveur à jour ({client.url})")


if __name__ == "__main__":
    main()
