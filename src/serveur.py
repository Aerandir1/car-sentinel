"""Serveur HTTP : la page du dashboard, les logos et l'API JSON."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import donnees
import synchro
import trajets
import voitures
from reseau import HOST

WEB = Path(__file__).resolve().parent.parent / "web"   # pages, scripts et fichiers servis au navigateur
LOGOS = WEB / "logo"


class QuietServer(ThreadingHTTPServer):
    """Ignore les connexions coupées."""

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_handler(state):
    """Base affichée (trajets, courbes) : celle de la voiture en cours, de sa démo en mode démo (state.car_db)."""
    pages = {"/": "dashboard.html", "/index.html": "dashboard.html", "/dashboard": "dashboard.html"}
    current_db = state.car_db

    def calib_info():
        """Mesures constructeur de la voiture pour l'onglet Étalonnage : réglage, et si le calculateur les donne."""
        labels = {k: (lbl, u) for k, lbl, u, *_ in state.car.METRICS}
        out = state.calib.describe()
        for key, c in out.items():
            c["label"], c["unit"] = labels[key]
            c["available"] = key in state.raw_keys
        return out

    def trip_metrics(db_path, trip):
        """Mesures d'un trajet, dans l'ordre des METRICS de la voiture, avec de quoi les tracer (libellé, unité,
        échelle, seuils). Lu dans la base : marche aussi sans la voiture. Une mesure constructeur validée compte si
        sa valeur brute est là (elle est recalculée avec l'étalonnage actuel, voir history)."""
        car = state.car
        counts = donnees.trip_keys(db_path, trip)
        out = []
        calc = {d["key"] for d in car.DERIVED}
        for k, lbl, u, _cmd, _conv, rng, warn, cold in car.METRICS + [
                (d["key"], d["label"], d["unit"], None, None, d["range"], None, None) for d in car.DERIVED]:
            n = counts.get(k, 0)
            if k in car.FALLBACK and state.calib.get(k)[2]:
                n = max(n, counts.get("brut." + k, 0))
            if n:
                src = state.sources.get(k) or ({"kind": "calc", "cmd": None} if k in calc else None)
                out.append({"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold,
                            "source": src, "n": n})
        return out

    def history(db_path, trip, key, from_ms, to_ms):
        """Courbe d'une métrique ; une mesure constructeur validée est recalculée depuis sa valeur brute avec
        l'étalonnage actuel (les trajets passés profitent d'un étalonnage corrigé)."""
        # (pas en démo : la démo enregistre aussi la vraie valeur, qu'on garde pour comparer)
        if key in state.car.FALLBACK and not state.get_settings().get("demo") and state.calib.get(key)[2]:
            h = donnees.history(db_path, trip, "brut." + key, from_ms, to_ms)
            if h["points"]:
                h["key"] = key
                h["points"] = [[t, None if v is None else state.calib.apply(key, v)] for t, v in h["points"]]
                return h
        return donnees.history(db_path, trip, key, from_ms, to_ms)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            # Le hub (page 3D, autre port) lit /api/state : autorisé pour les pages de cette machine seulement,
            # en local ou, avec --lan, appelée par le même nom depuis le réseau (http://pc.local:8000 → :8080)
            origin = self.headers.get("Origin", "")
            hostname = urlsplit(origin).hostname or ""
            if (hostname in ("127.0.0.1", "localhost") or hostname.endswith(".localhost")
                    or hostname == urlsplit("//" + self.headers.get("Host", "")).hostname):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Une seule origine (HOST) : les réglages du navigateur (localStorage) sont rangés par origine.
            # Seules les pages sont redirigées : l'API reste lisible par le hub sur 127.0.0.1.
            path = urlsplit(self.path).path
            host = urlsplit("//" + self.headers.get("Host", ""))
            if path in pages and host.hostname in ("127.0.0.1", "localhost", "::1"):
                port = f":{host.port}" if host.port else ""
                self.send_response(302)
                self.send_header("Location", f"http://{HOST}{port}{self.path}")
                self.end_headers()
                return
            if path in pages:
                self._send(200, (WEB / pages[path]).read_bytes(), "text/html; charset=utf-8")
            elif path.startswith("/logo/"):
                # Les deux résolus : sur Android, le dossier de l'appli passe par un lien (/data/user/0 → /data/data)
                f = (LOGOS / path[6:]).resolve()
                if f.is_file() and f.is_relative_to(LOGOS.resolve()) and f.suffix == ".svg":
                    self._send(200, f.read_bytes(), "image/svg+xml")
                else:
                    self._send(404, b"not found", "text/plain")
            elif path in ("/api/trips", "/api/history", "/api/trips/keys"):
                db_path = current_db()
                if not db_path:
                    self._send(404, b"enregistrement desactive (--no-db)", "text/plain")
                    return
                q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                try:
                    num = lambda k: int(q[k]) if q.get(k) else None
                    if path == "/api/trips":
                        body = donnees.trips(db_path, num("limit") or 200)
                        for t in body:   # détail du score d'éco-conduite : points perdus sur chaque défaut
                            p = trajets.eco_penalties(t) if t["drive_s"] is not None else None
                            t["eco_penalties"] = p and {k: round(v, 1) for k, v in p.items()}
                    elif path == "/api/trips/keys":
                        body = trip_metrics(db_path, q.get("trip", ""))
                    else:
                        body = history(db_path, q.get("trip") or None, q.get("key", ""), num("from"), num("to"))
                except ValueError:
                    self._send(400, b"parametre invalide", "text/plain")
                    return
                self._send(200, json.dumps(body).encode(), "application/json")
            elif path == "/api/state":
                self._send(200, json.dumps(state.snapshot()).encode(), "application/json")
            elif path == "/api/meta":
                self._send(200, json.dumps(state.meta()).encode(), "application/json")
            elif path == "/api/settings":
                self._send(200, json.dumps(state.get_settings()).encode(), "application/json")
            elif path == "/api/calib":
                self._send(200, json.dumps(calib_info()).encode(), "application/json")
            elif path == "/api/prices":
                self._send(200, json.dumps(state.prices()).encode(), "application/json")
            elif path in ("/api/diag", "/api/diag/scan", "/api/diag/compare"):
                q = parse_qs(urlsplit(self.path).query)
                try:
                    if path == "/api/diag":
                        body = state.diag.status()
                    elif path == "/api/diag/scan":
                        body = state.diag.load_scan(q.get("name", [""])[0])
                    else:
                        body = state.diag.compare(q.get("f", []))
                except (ValueError, OSError) as e:
                    self._send(400, str(e).encode(), "text/plain; charset=utf-8")
                    return
                self._send(200, json.dumps(body).encode(), "application/json")
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/api/reset":
                state.reset_minmax()
                self._send(204, b"", "text/plain")
            elif self.path == "/api/settings":
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    if str(body.get("server") or "").strip():
                        synchro.Client(str(body["server"]))   # adresse refusée avant d'être gardée
                    car = body.pop("car", None)
                    if car and car not in voitures.CARS:
                        raise ValueError("voiture inconnue")
                    if car and car != state.car.ID:
                        # Voiture branchée : c'est son VIN qui décide (sauf en démo, qui simule la voiture choisie)
                        if state.ecu == "ok" and not state.get_settings().get("demo"):
                            self._send(409, "voiture reconnue au VIN : elle ne se change pas pendant qu'elle est"
                                            " branchée".encode(), "text/plain; charset=utf-8")
                            return
                        state.set_car(car, "choisie dans Réglages")
                    saved = state.set_settings(body)
                except synchro.SyncError as e:
                    self._send(400, str(e).encode(), "text/plain; charset=utf-8")
                    return
                except (ValueError, TypeError, AttributeError):
                    self._send(400, b"JSON invalide", "text/plain")
                    return
                self._send(200, json.dumps(saved).encode(), "application/json")
            elif self.path == "/api/calib":
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    state.calib.save(str(body["key"]), body)
                except (ValueError, TypeError, KeyError, AttributeError) as e:
                    self._send(400, f"étalonnage invalide : {e}".encode(), "text/plain; charset=utf-8")
                    return
                state.calib_changed()
                self._send(200, json.dumps(calib_info()).encode(), "application/json")
            elif self.path == "/api/price":
                # Prix d'une voiture validé dans Réglages → Carburant : gardé ici, puis poussé tout de suite au
                # serveur (sinon au retour du serveur) ; la réponse dit s'il est parti, pour la notification
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    car, price = str(body["car"]), float(body["price"])
                    if car not in voitures.CARS:
                        raise ValueError("voiture inconnue")
                    if not 0 < price <= 10:
                        raise ValueError("prix entre 0 et 10 €/L attendu")
                except (ValueError, TypeError, KeyError) as e:
                    self._send(400, f"prix refusé : {e}".encode(), "text/plain; charset=utf-8")
                    return
                state.set_settings({f"fuel_price_{car}": price})
                sent, error = synchro.push_price(state, car)
                self._send(200, json.dumps({"car": car, "name": voitures.get(car).NAME, "price": price, "sent": sent,
                                            "error": error, "prices": state.prices()}).encode(), "application/json")
            elif self.path in ("/api/diag/start", "/api/diag/stop"):
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    if self.path == "/api/diag/start":
                        state.diag.start(body)
                    else:
                        state.diag.stop()
                except (ValueError, TypeError, AttributeError) as e:
                    self._send(400, str(e).encode(), "text/plain; charset=utf-8")
                    return
                self._send(200, json.dumps(state.diag.status()).encode(), "application/json")
            elif self.path == "/api/trips/note" and current_db():
                try:
                    db_path = current_db()
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    found = donnees.set_note(db_path, str(body["id"]), str(body.get("note") or "")[:500])
                except (ValueError, TypeError, KeyError):
                    self._send(400, b"JSON invalide", "text/plain")
                    return
                self._send(204 if found else 404, b"" if found else b"trajet inconnu", "text/plain")
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *_):
            pass

    return Handler
