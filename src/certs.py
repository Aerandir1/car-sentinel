"""Certificat HTTPS pour le serveur local."""

import shutil
import subprocess
from pathlib import Path

CERTS = Path(__file__).resolve().parent.parent / "certs"
# Nom de la page : les navigateurs envoient tout nom en .localhost vers la machine elle-même (pas de /etc/hosts)
HOST = "clio-sentinel.localhost"


def ensure_cert():
    """Certificat HTTPS pour clio-sentinel.localhost, 127.0.0.1 et localhost, créé au premier lancement dans certs/.

    Avec mkcert installé, le certificat est signé par l'autorité locale de mkcert : le navigateur
    l'accepte sans avertissement. Sinon, certificat auto-signé OpenSSL (avertissement à accepter).
    Un certificat d'avant le nom clio-sentinel.localhost est refait.
    """
    CERTS.mkdir(exist_ok=True)
    names = [HOST, "127.0.0.1", "localhost", "::1"]
    if shutil.which("mkcert"):
        crt, key = CERTS / "mkcert.pem", CERTS / "mkcert-key.pem"
        if not (crt.is_file() and key.is_file() and _covers(crt)):
            subprocess.run(["mkcert", "-cert-file", str(crt), "-key-file", str(key), *names], check=True, capture_output=True)
        return crt, key, True
    crt, key = CERTS / "selfsigned.pem", CERTS / "selfsigned-key.pem"
    if not (crt.is_file() and key.is_file() and _covers(crt)):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                        "-subj", "/CN=Dashboard Clio", "-addext", f"subjectAltName=DNS:{HOST},IP:127.0.0.1,DNS:localhost,IP:::1",
                        "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)
    key.chmod(0o600)
    return crt, key, False


def _covers(crt):
    """Le certificat porte-t-il le nom HOST ?"""
    out = subprocess.run(["openssl", "x509", "-in", str(crt), "-noout", "-ext", "subjectAltName"],
                         capture_output=True, text=True)
    return f"DNS:{HOST}" in out.stdout
