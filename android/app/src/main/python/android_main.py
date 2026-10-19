"""Lancement du dashboard sur Android (appelé par SentinelService.java) : le code du PC (src/), avec les
fichiers dans le dossier de l'appli et la clé ELM327 USB par androidusb:// (car_usb). La voiture branchée est
reconnue au VIN, comme sur le PC."""

import os
import sys
from pathlib import Path

import serial

serial.protocol_handler_packages.append("car_usb")


def main(files_dir, web_dir, version, server="", name=""):
    files = Path(files_dir)
    data = files / "data"
    data.mkdir(parents=True, exist_ok=True)
    os.environ["CAR_VERSION"] = version
    # Serveur d'office (SERVEUR du car.conf du PC à la compilation) et nom de la tablette sur sa page :
    # files/car.conf puis l'onglet Réglages passent devant
    os.environ.setdefault("CAR_SERVER", server)
    os.environ.setdefault("CAR_SERVER_NAME", name)

    import car_sentinel
    import config
    import requetes
    import serveur

    config.CONF = files / "car.conf"        # réglages facultatifs (SERVEUR…) ; pas de modèle à copier
    config.EXEMPLE = files / "car.conf.exemple"
    car_sentinel.DATA = data
    car_sentinel.version = lambda: version   # pas de git sur Android (commit lu à la compilation)
    requetes.SCAN_DIR = data
    serveur.WEB = Path(web_dir)
    serveur.LOGOS = serveur.WEB / "logo"

    sys.argv = ["car_sentinel", "--port", "androidusb://", "--data", str(data)]
    car_sentinel.main()
