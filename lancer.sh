#!/usr/bin/env bash
# Ouvre le dashboard Clio dans le navigateur, en le lançant s'il ne tourne pas déjà.
#
#   ./lancer.sh                 # service installé (service.sh) : le relance au besoin ; sinon lance le script ici
#   ./lancer.sh --port /dev/ttyUSB0 --log trajet.csv     # lance le script ici avec ces options (sans le service)
#
# Réglages dans fiesta.conf ; les options passées ici vont à src/dashboard_fiesta.py et passent devant (voir README).
# NO_BROWSER=1 ./lancer.sh  pour ne pas ouvrir le navigateur.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

VENV="${VENV:-$HOME/.venvs/obd}"
UNIT="$HOME/.config/systemd/user/fiesta-sentinel.service"

# Port de la page : --http s'il est passé, sinon PORT_PAGE de fiesta.conf (voir fiesta.conf.exemple)
http_port=$(sed -n 's/^[[:space:]]*PORT_PAGE[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' fiesta.conf 2>/dev/null | tail -n 1)
http_port=${http_port:-8082}; prev=""
for a in "$@"; do
    [[ "$prev" == "--http" ]] && http_port="$a"
    [[ "$a" == --http=* ]] && http_port="${a#--http=}"
    prev="$a"
done
page="http://fiesta-sentinel.localhost:$http_port"

running() {   # le dashboard répond-il (et pas un autre programme sur ce port) ?
    curl -sf --max-time 1 "http://127.0.0.1:$http_port/api/state" 2>/dev/null | grep -q '"status"'
}

open_browser() {
    [[ -n "${NO_BROWSER:-}" ]] && return
    echo "→ Page : $page"
    if command -v chromium >/dev/null; then
        chromium "$page" >/dev/null 2>&1 &
    else
        xdg-open "$page" >/dev/null 2>&1 &
    fi
}

# Déjà en route : on ouvre juste la page.
if [[ $# -eq 0 ]] && running; then
    open_browser
    exit 0
fi

# Service installé et pas d'options : c'est lui qui fait tourner le script (pas de deuxième copie ici).
if [[ $# -eq 0 && -f "$UNIT" ]]; then
    ./service.sh restart && open_browser
    exit
fi

# Deux copies se disputeraient l'ELM327 et la base
if systemctl --user is-active -q fiesta-sentinel 2>/dev/null; then
    echo "Le service tourne déjà : ./service.sh stop d'abord pour lancer le script ici avec des options"
    echo "(ou les mettre dans AUTRES_OPTIONS de fiesta.conf, puis ./service.sh restart)"
    exit 1
fi

# Environnement Python : créé et rempli au premier lancement.
if ! "$VENV/bin/python" -c "import obd" 2>/dev/null; then
    echo "→ Installation de Python et des dépendances dans $VENV"
    python -m venv "$VENV"
    "$VENV/bin/python" -m pip install -q -r requirements.txt
fi

# Ouvre le navigateur une fois le serveur prêt.
(
    for _ in $(seq 1 30); do
        running && { open_browser; break; }
        sleep 0.5
    done
) &

echo "→ Lancement du dashboard (Ctrl+C pour arrêter)"
exec "$VENV/bin/python" src/dashboard_fiesta.py "$@"
