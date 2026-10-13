"""Serveur HTTP : la page du dashboard, les logos et l'API JSON."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import donnees
import ford
from etat import DERIVED, METRICS
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


def make_handler(state, current_db):
    """current_db() : base affichée (trajets, courbes), celle de la démo en mode démo ; None avec --no-db."""
    pages = {"/": "dashboard.html", "/index.html": "dashboard.html", "/dashboard": "dashboard.html"}
    labels = {k: (lbl, u) for k, lbl, u, *_ in METRICS}

    def calib_info():
        """Mesures Ford pour l'onglet Étalonnage : réglage, et si le calculateur les donne (available)."""
        out = state.calib.describe()
        for key, c in out.items():
            c["label"], c["unit"] = labels[key]
            c["available"] = key in state.raw_keys
        return out

    def trip_metrics(db_path, trip):
        """Mesures d'un trajet, dans l'ordre de METRICS, avec de quoi les tracer (libellé, unité, échelle, seuils).
        Lu dans la base : marche aussi sans la voiture. Une mesure Ford validée compte si sa valeur brute est là
        (elle est recalculée avec l'étalonnage actuel, voir history)."""
        counts = donnees.trip_keys(db_path, trip)
        out = []
        for k, lbl, u, _cmd, _conv, rng, warn, cold in METRICS + [
                (DERIVED["key"], DERIVED["label"], DERIVED["unit"], None, None, DERIVED["range"], None, None)]:
            n = counts.get(k, 0)
            if k in ford.FALLBACK and state.calib.get(k)[2]:
                n = max(n, counts.get("brut." + k, 0))
            if n:
                src = state.sources.get(k) or ({"kind": "calc", "cmd": None} if k == DERIVED["key"] else None)
                out.append({"key": k, "label": lbl, "unit": u, "range": rng, "warn": warn, "cold": cold,
                            "source": src, "n": n})
        return out

    def history(db_path, trip, key, from_ms, to_ms):
        """Courbe d'une métrique ; une mesure Ford validée est recalculée depuis sa valeur brute avec
        l'étalonnage actuel (les trajets passés profitent d'un étalonnage corrigé)."""
        # (pas en démo : la démo enregistre aussi la vraie valeur, qu'on garde pour comparer)
        if key in ford.FALLBACK and not state.get_settings().get("demo") and state.calib.get(key)[2]:
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
            # en local ou, avec --lan, appelée par le même nom depuis le réseau (http://fiesta.local:8000 → :8082)
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
                f = (LOGOS / path[6:]).resolve()
                if f.is_file() and f.is_relative_to(LOGOS) and f.suffix == ".svg":
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
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path == "/api/reset":
                state.reset_minmax()
                self._send(204, b"", "text/plain")
            elif self.path == "/api/settings":
                try:
                    body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                    saved = state.set_settings(body)
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
