#!/usr/bin/env python3
"""Python embarqué dans l'appli Windows : la distribution « embeddable » de python.org (Windows 64 bits) et les
dépendances du dashboard (celles de l'appli Android), dans desktop/build/python, plus la version du code
(git describe) dans desktop/build/version.txt. Lancé par npm run dist ; marche depuis Linux comme depuis Windows.
"""

import io
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

PYTHON = "3.14.8"
PAQUETS = ["obd==0.7.3", "pyserial==3.5"]

ICI = Path(__file__).resolve().parent.parent
BUILD = ICI / "build"
DEST = BUILD / "python"


def main():
    court = "".join(PYTHON.split(".")[:2])   # 314
    if not (DEST / "python.exe").is_file() or not (DEST / f"python{court}.zip").is_file():
        url = f"https://www.python.org/ftp/python/{PYTHON}/python-{PYTHON}-embed-amd64.zip"
        print(f"→ {url}")
        shutil.rmtree(DEST, ignore_errors=True)
        with urllib.request.urlopen(url) as r:
            zipfile.ZipFile(io.BytesIO(r.read())).extractall(DEST)
    # Chemins de Python : sa bibliothèque, les paquets et desktop_main.py ; le code (src/) est ajouté par celui-ci
    (DEST / f"python{court}._pth").write_text(f"python{court}.zip\n.\nLib\\site-packages\n", encoding="ascii")

    site = DEST / "Lib" / "site-packages"
    shutil.rmtree(site, ignore_errors=True)
    print(f"→ {', '.join(PAQUETS)}")
    subprocess.run([sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", "--no-compile",
                    "--target", str(site), "--platform", "win_amd64", "--python-version", PYTHON,
                    "--implementation", "cp", "--only-binary=:all:", *PAQUETS], check=True)

    try:
        version = subprocess.run(["git", "-C", str(ICI), "describe", "--always", "--dirty"], capture_output=True,
                                 text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        version = "?"
    (BUILD / "version.txt").write_text(version, encoding="utf-8")
    print(f"Python {PYTHON} embarqué dans {DEST} (code {version})")


if __name__ == "__main__":
    main()
