"""Lancement du dashboard sur Android (appelé par SentinelService.java) : le code du PC (src/), avec les
fichiers dans le dossier de l'appli et la clé ELM327 USB par androidusb:// (fiesta_usb)."""

import os
import sys
from pathlib import Path

import serial

serial.protocol_handler_packages.append("fiesta_usb")


def main(files_dir, web_dir, version, server="", name=""):
    files = Path(files_dir)
    data = files / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["FIESTA_VERSION"] = version
    # Serveur d'office (SERVEUR du fiesta.conf du PC à la compilation) et nom de la tablette sur sa page :
    # files/fiesta.conf puis l'onglet Réglages passent devant
    os.environ.setdefault("FIESTA_SERVER", server)
    os.environ.setdefault("FIESTA_SERVER_NAME", name)

    import config
    import dashboard_fiesta
    import requetes
    import serveur

    config.CONF = files / "fiesta.conf"        # réglages facultatifs (SERVEUR…) ; pas de modèle à copier
    config.EXEMPLE = files / "fiesta.conf.exemple"
    dashboard_fiesta.DATA = data
    dashboard_fiesta.version = lambda: version   # pas de git sur Android (commit lu à la compilation)
    requetes.SCAN_FILE = data / "scan-pid.json"
    serveur.WEB = Path(web_dir)
    serveur.LOGOS = serveur.WEB / "logo"

    sys.argv = ["dashboard_fiesta", "--port", "androidusb://", "--db", str(data / "fiesta.db")]
    dashboard_fiesta.main()
