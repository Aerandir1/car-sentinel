"""Certificat HTTPS pour le serveur local."""

import ipaddress
import json
import re
import os
import shutil
import socket
import subprocess
from pathlib import Path

CERTS = Path(__file__).resolve().parent.parent / "certs"
# Nom de la page : les navigateurs envoient tout nom en .localhost vers la machine elle-même (pas de /etc/hosts)
HOST = "clio-sentinel.localhost"
LOCAL_NAMES = [HOST, "127.0.0.1", "localhost", "::1"]


def lan_names():
    """Noms sous lesquels les autres appareils du réseau voient cette machine : <nom>.local (mDNS, avahi)
    et ses adresses IPv4, plus CLIO_CERT_NAMES (séparés par des virgules ; Docker ne voit pas les IP de l'hôte)."""
    names = []
    if not Path("/.dockerenv").exists():   # dans un conteneur, nom et IP sont ceux du conteneur : inutiles
        names.append(f"{socket.gethostname().split('.')[0]}.local")
        try:
            out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5)
            names += [a["local"] for i in json.loads(out.stdout or "[]") for a in i.get("addr_info", [])
                      if a.get("scope") == "global"]
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
    names += [n.strip() for n in os.environ.get("CLIO_CERT_NAMES", "").split(",") if n.strip()]
    return list(dict.fromkeys(names))


def ensure_cert(extra_names=()):
    """Certificat HTTPS pour clio-sentinel.localhost, 127.0.0.1, localhost et extra_names, créé dans certs/.

    Avec mkcert installé, le certificat est signé par l'autorité locale de mkcert : le navigateur
    l'accepte sans avertissement. Sinon, certificat auto-signé OpenSSL (avertissement à accepter).
    Un certificat auquel il manque un des noms (nouvelle adresse IP sur le réseau, par exemple) est refait.
    """
    CERTS.mkdir(exist_ok=True)
    names = list(dict.fromkeys([*LOCAL_NAMES, *extra_names]))
    if shutil.which("mkcert"):
        crt, key = CERTS / "mkcert.pem", CERTS / "mkcert-key.pem"
        if not (crt.is_file() and key.is_file() and _covers(crt, names)):
            subprocess.run(["mkcert", "-cert-file", str(crt), "-key-file", str(key), *names], check=True, capture_output=True)
        return crt, key, True
    crt, key = CERTS / "selfsigned.pem", CERTS / "selfsigned-key.pem"
    if not (crt.is_file() and key.is_file() and _covers(crt, names)):
        san = ",".join(f"{'IP' if _is_ip(n) else 'DNS'}:{n}" for n in names)
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825",
                        "-subj", "/CN=Dashboard Clio", "-addext", f"subjectAltName={san}",
                        "-keyout", str(key), "-out", str(crt)], check=True, capture_output=True)
    key.chmod(0o600)
    return crt, key, False


def _is_ip(name):
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def _san(name):
    """Forme normalisée d'un nom du certificat : ("IP", "::1") ou ("DNS", "localhost")."""
    return ("IP", str(ipaddress.ip_address(name))) if _is_ip(name) else ("DNS", name.lower())


def _covers(crt, names):
    """Le certificat porte-t-il tous ces noms ?"""
    out = subprocess.run(["openssl", "x509", "-in", str(crt), "-noout", "-ext", "subjectAltName"],
                         capture_output=True, text=True)
    sans = {_san(v) for v in re.findall(r"(?:DNS|IP Address):([^,\s]+)", out.stdout)}
    return all(_san(n) in sans for n in names)
