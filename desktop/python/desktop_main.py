"""Lancement du dashboard par l'appli Windows (desktop/main.js) : le code du PC (src/), avec réglages, bases et
journal dans le dossier de l'utilisateur (%APPDATA%\\Car Sentinel) plutôt qu'à côté du programme, qu'une mise à
jour remplace. Comme android_main.py pour la tablette.

    pythonw desktop_main.py <dossier utilisateur> <version>
"""

import os
import sys
import time
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"   # appli installée : resources\src ; sinon desktop/../src
if not SRC.is_dir():
    SRC = Path(__file__).resolve().parent.parent.parent / "src"
sys.path.insert(0, str(SRC))


def main(user_dir, version):
    files = Path(user_dir)
    data, logs = files / "data", files / "logs"
    data.mkdir(parents=True, exist_ok=True)
    logs.mkdir(exist_ok=True)
    if sys.stdout is None:   # pythonw : pas de console, le journal va dans logs\car.log (menu « Journal »)
        sys.stdout = sys.stderr = open(logs / "car.log", "a", encoding="utf-8", buffering=1)
        print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} ---")
    os.environ["CAR_VERSION"] = version

    import car_sentinel
    import config
    import requetes

    config.CONF = files / "car.conf"   # créé depuis car.conf.exemple au premier lancement (menu « Réglages »)
    car_sentinel.DATA = data
    car_sentinel.version = lambda: version
    requetes.SCAN_DIR = data

    sys.argv = ["car_sentinel", "--data", str(data)]
    car_sentinel.main()


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "?")
