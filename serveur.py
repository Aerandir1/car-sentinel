"""Serveur HTTP : la page du dashboard, les logos et l'API JSON."""

import json
import ssl
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import donnees

WEB = Path(__file__).resolve().parent / "web"   # pages, scripts et fichiers servis au navigateur
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
            if urlsplit(origin).hostname in ("127.0.0.1", "localhost"):
                self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Une seule origine (127.0.0.1) : les réglages du navigateur (localStorage) sont rangés par origine.
            host = self.headers.get("Host", "")
            if host.startswith("localhost"):
                self.send_response(302)
                self.send_header("Location", f"{scheme}://127.0.0.1{host[9:]}{self.path}")
                self.end_headers()
                return
            path = urlsplit(self.path).path
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
                    if path == "/api/trips":
                        body = donnees.trips(db_path)
                    else:
                        num = lambda k: int(q[k]) if q.get(k) else None
                        body = donnees.history(db_path, num("trip"), q.get("key", ""), num("from"), num("to"))
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
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *_):
            pass

    return Handler
