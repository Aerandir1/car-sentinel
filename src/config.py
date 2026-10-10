"""Réglages de clio.conf (modèle : clio.conf.exemple), traduits en options du script.

Le fichier est créé depuis le modèle au premier lancement. Dans Docker, il n'est pas dans l'image :
les réglages arrivent en variables d'environnement (env_file du docker-compose.yml), qui passent avant le fichier.
"""

import os
import shlex
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
CONF = HERE / "clio.conf"
EXEMPLE = HERE / "clio.conf.exemple"
IN_DOCKER = Path("/.dockerenv").exists()

# réglage → option du script
OPTIONS = {"PORT_PAGE": "--http", "ADAPTATEUR": "--port", "DEBIT": "--baud", "SERVEUR": "--server",
           "NOM_PC": "--server-name"}
FLAGS = {"RESEAU": ("--lan", None)}
KEYS = [*OPTIONS, *FLAGS, "AUTRES_OPTIONS"]


def read(path=CONF):
    """KEY=VALUE par ligne, # pour les commentaires ; guillemets autour de la valeur facultatifs."""
    conf = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = (s.strip() for s in line.split("=", 1))
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            conf[key] = value
    return conf


def _yes(key, value):
    v = value.lower()
    if v in ("oui", "o", "yes", "y", "1", "true", "vrai"):
        return True
    if v not in ("non", "n", "no", "0", "false", "faux"):
        print(f"Attention : clio.conf : {key}={value} (attendu : oui ou non), compris comme non")
    return False


def argv():
    """Options du script tirées de clio.conf (et de l'environnement dans Docker), à mettre avant celles de la
    ligne de commande : celles-ci passent devant."""
    if not IN_DOCKER and not CONF.exists() and EXEMPLE.is_file():
        shutil.copyfile(EXEMPLE, CONF)
        print(f"Réglages créés dans {CONF} (à modifier, puis relancer)")
    conf = read()
    conf.update({k: os.environ[k] for k in KEYS if k in os.environ})
    out = []
    for key, opt in OPTIONS.items():
        if conf.get(key):
            out += [opt, conf[key]]
    for key, (on, off) in FLAGS.items():
        if conf.get(key):
            flag = on if _yes(key, conf[key]) else off
            if flag:
                out.append(flag)
    try:
        return out + shlex.split(conf.get("AUTRES_OPTIONS", ""))
    except ValueError as e:   # guillemet non fermé…
        print(f"Attention : clio.conf : AUTRES_OPTIONS illisible ({e}), ignoré")
        return out
