"""Serveur HTTP : la page du dashboard, les logos et l'API JSON."""

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import donnees
from certs import HOST

WEB = Path(__file__).resolve().parent.parent / "web"   # pages, scripts et fichiers servis au navigateur
LOGOS = WEB / "logo"


class QuietServer(ThreadingHTTPServer):
    """Ignore les connexions coupées et les clients qui parlent HTTP au port HTTPS."""

    def handle_error(self, request, client_address):
        import sys
        if isinstance(sys.exc_info()[1], (ssl.SSLError, ConnectionError, TimeoutError)):
            return
        super().handle_error(request, client_address)


def make_handler(state, scheme, db_path):
    pages = {"/": "dashboard.html", "/index.html": "dashboard.html", "/dashboard": "dashboard.html"}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            # Le hub (page 3D, autre port) lit /api/state : autorisé pour les pages locales seulement
            origin = self.headers.get("Origin", "")
            hostname = urlsplit(origin).hostname or ""
            if hostname in ("127.0.0.1", "localhost") or hostname.endswith(".localhost"):
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
                self.send_header("Location", f"{scheme}://{HOST}{port}{self.path}")
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
            elif path in ("/api/trips", "/api/history"):
                if not db_path:
                    self._send(404, b"enregistrement desactive (--no-db)", "text/plain")
                    return
                q = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                try:
                    num = lambda k: int(q[k]) if q.get(k) else None
                    if path == "/api/trips":
                        body = donnees.trips(db_path, num("limit") or 200)
                    else:
                        body = donnees.history(db_path, q.get("trip") or None, q.get("key", ""), num("from"), num("to"))
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
            elif self.path == "/api/trips/note" and db_path:
                try:
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
