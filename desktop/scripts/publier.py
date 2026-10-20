#!/usr/bin/env python3
"""Publie la dernière version de l'appli Windows sur clio-sentinel-server : les PC qui ont Car Sentinel la
trouvent au prochain contact avec le serveur (maison ou VPN), la téléchargent et l'installent.

    npm run dist        # dist/car-sentinel-setup-X.Y.Z.exe, .blockmap et latest.yml
    npm run publier     # les envoie au serveur (PUT /maj/…)

Serveur : les adresses de SERVEUR dans ../car.conf (la première qui répond), ou --server URL.
Jeton : celui de MAJ_JETON sur le serveur, dans la variable CAR_MAJ_JETON ou le fichier desktop/.maj-jeton.
"""

import argparse
import http.client
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ICI = Path(__file__).resolve().parent.parent
DIST = ICI / "dist"
CONF = ICI.parent / "car.conf"
JETON = ICI / ".maj-jeton"
PORT = 8207


def adresses(arg):
    """Adresses du serveur : --server, sinon SERVEUR de car.conf, en http://IP:PORT."""
    brut = arg
    if not brut and CONF.exists():
        m = re.search(r"^\s*SERVEUR\s*=\s*[\"']?([^\"'\n#]*)", CONF.read_text(encoding="utf-8"), re.M)
        brut = m.group(1) if m else ""
    out = []
    for a in filter(None, (x.strip().rstrip("/") for x in (brut or "").split(","))):
        u = urlsplit(a if "://" in a else "http://" + a)
        out.append((u.hostname, u.port or PORT))
    return out


def requete(hote, methode, chemin, corps=None, entetes=None, timeout=5):
    conn = http.client.HTTPConnection(*hote, timeout=timeout)
    try:
        conn.request(methode, chemin, corps, entetes or {})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def joignable(hotes):
    for h in hotes:
        try:
            if requete(h, "GET", "/api/server", timeout=3)[0] == 200:
                return h
        except OSError as e:
            print(f"  {h[0]}:{h[1]} ne répond pas ({e})")
    return None


def version_de(yml):
    m = re.search(r"^version:\s*['\"]?([^'\"\s]+)", yml, re.M)
    return m.group(1) if m else None


def cle(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.split(r"[.-]", v or "0"))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--server", help="adresse du serveur (défaut : SERVEUR de car.conf)")
    p.add_argument("--force", action="store_true", help="publier même si le serveur a déjà cette version ou plus récente")
    args = p.parse_args()

    version = json.loads((ICI / "package.json").read_text(encoding="utf-8"))["version"]
    latest = DIST / "latest.yml"
    if not latest.exists():
        sys.exit("dist/latest.yml absent : lancer d'abord npm run dist")
    yml = latest.read_text(encoding="utf-8")
    if version_de(yml) != version:
        sys.exit(f"dist/latest.yml est la version {version_de(yml)}, package.json la {version} : relancer npm run dist")
    m = re.search(r"^path:\s*['\"]?([^'\"\n]+?)['\"]?\s*$", yml, re.M)
    exe = DIST / m.group(1) if m else None
    if not exe or not exe.exists():
        sys.exit(f"installeur de latest.yml introuvable dans dist/ ({m and m.group(1)})")
    if exe.stat().st_size < 10 * 1024 * 1024:
        sys.exit(f"{exe.name} ne fait que {exe.stat().st_size // 1024} Ko : installeur incomplet "
                 "(construit sous Linux sans Wine ?)")
    fichiers = [exe] + [f for f in [exe.with_name(exe.name + ".blockmap")] if f.exists()] + [latest]

    jeton = os.environ.get("CAR_MAJ_JETON") or (JETON.read_text(encoding="utf-8").strip() if JETON.exists() else "")
    if not jeton:
        sys.exit(f"pas de jeton : le mettre dans {JETON} (ou CAR_MAJ_JETON), le même que MAJ_JETON du serveur")

    hotes = adresses(args.server)
    if not hotes:
        sys.exit("pas d'adresse de serveur : SERVEUR dans car.conf, ou --server 10.100.0.1")
    hote = joignable(hotes)
    if not hote:
        sys.exit("serveur injoignable (Wi-Fi de la maison ou VPN ?)")
    print(f"Serveur http://{hote[0]}:{hote[1]}")

    code, corps = requete(hote, "GET", "/maj/latest.yml")
    actuelle = version_de(corps.decode("utf-8", "replace")) if code == 200 else None
    print(f"  version publiée : {actuelle or 'aucune'} ; à publier : {version}")
    if actuelle and cle(actuelle) >= cle(version) and not args.force:
        sys.exit("le serveur a déjà cette version ou une plus récente : augmenter \"version\" dans package.json "
                 "(npm version patch --no-git-tag-version), relancer npm run dist (ou --force)")

    # Jeton vérifié d'abord (envoi vide, nom refusé : 400 si le jeton passe, 403 sinon) : un refus en plein envoi
    # de l'installeur ne se verrait que comme une connexion coupée
    code, rep = requete(hote, "PUT", "/maj/verification", b"", {"Authorization": f"Bearer {jeton}"})
    if code == 403:
        sys.exit(f"refusé par le serveur : {rep.decode('utf-8', 'replace')}")

    # latest.yml en dernier : les PC ne voient la nouvelle version qu'une fois l'installeur complet sur le serveur
    for f in fichiers:
        taille = f.stat().st_size
        print(f"  envoi de {f.name} ({taille / 1024 / 1024:.1f} Mo)…", flush=True)
        with f.open("rb") as corps:
            code, rep = requete(hote, "PUT", f"/maj/{f.name}", corps,
                                {"Authorization": f"Bearer {jeton}", "Content-Length": str(taille),
                                 "Content-Type": "application/octet-stream"}, timeout=120)
        if code != 200:
            sys.exit(f"refusé par le serveur ({code}) : {rep.decode('utf-8', 'replace')}")
    print(f"Version {version} publiée : les PC la prennent au prochain contact avec le serveur (6 h au plus), "
          "ou tout de suite par « Rechercher une mise à jour » dans le menu de l'icône.")


if __name__ == "__main__":
    main()
