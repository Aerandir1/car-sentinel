"""Certificat HTTPS pour le serveur local."""

import shutil
import subprocess
from pathlib import Path

CERTS = Path(__file__).resolve().parent / "certs"


def ensure_cert():
    """Certificat HTTPS pour 127.0.0.1 et localhost, créé au premier lancement dans certs/.

    Avec mkcert installé, le certificat est signé par l'autorité locale de mkcert : le navigateur
    l'accepte sans avertissement. Sinon, certificat auto-signé OpenSSL (avertissement à accepter).
    """
    CERTS.mkdir(exist_ok=True)
    names = ["127.0.0.1", "localhost", "::1"]
    if shutil.which("mkcert"):
        crt, key = CERTS / "mkcert.pem", CERTS / "mkcert-key.pem"
        if not (crt.is_file() and key.is_file()):
            subprocess.run(["mkcert", "-cert-file", str(crt), "-key-file", str(key), *names], check=True, capture_output=True)
        return crt, key, True
    crt, key = CERTS / "selfsigned.pem", CERTS / "selfsigned-key.pem"
    if not (crt.is_file() and key.is_file()):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                        "-subj", "/CN=Dashboard Clio", "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost,IP:::1",
                        "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)
    key.chmod(0o600)
    return crt, key, False
