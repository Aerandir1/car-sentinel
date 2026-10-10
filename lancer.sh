#!/usr/bin/env bash
# Lance le serveur du dashboard Clio.
#
#   ./lancer.sh                 # dans la voiture (ELM327 auto-détecté)
#   ./lancer.sh --port /dev/ttyUSB0 --log trajet.csv
#
# Réglages dans clio.conf ; les options passées ici vont à src/dashboard_clio.py et passent devant (voir README).
# NO_BROWSER=1 ./lancer.sh  pour ne pas ouvrir le navigateur.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

VENV="${VENV:-$HOME/.venvs/obd}"

# Environnement Python : créé et rempli au premier lancement.
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "→ Création du venv $VENV"
    python -m venv "$VENV"
fi
PY="$VENV/bin/python"
if ! "$PY" -c "import obd" 2>/dev/null; then
    echo "→ Installation des dépendances"
    "$PY" -m pip install -q -r requirements.txt
fi

args=("$@")

# Port de la page : --http s'il est passé, sinon PORT_PAGE de clio.conf (voir clio.conf.exemple)
http_port=$(sed -n 's/^[[:space:]]*PORT_PAGE[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' clio.conf 2>/dev/null | tail -n 1)
http_port=${http_port:-8080}; prev=""
for a in "${args[@]}"; do
    [[ "$prev" == "--http" ]] && http_port="$a"
    [[ "$a" == --http=* ]] && http_port="${a#--http=}"
    prev="$a"
done

url="http://127.0.0.1:$http_port"                    # pour vérifier si le script tourne
page="http://clio-sentinel.localhost:$http_port"     # ce qu'on ouvre dans le navigateur

open_browser() {
    echo "→ Page : $page"
    if command -v chromium >/dev/null; then
        chromium "$page" >/dev/null 2>&1 &
    else
        xdg-open "$page" >/dev/null 2>&1 &
    fi
}

# Déjà lancé (service, voir service.sh) : on ouvre juste la page.
if curl -s -o /dev/null --max-time 2 "$url"; then
    echo "→ Dashboard déjà en route"
    [[ -z "${NO_BROWSER:-}" ]] && open_browser
    exit 0
fi

# Ouvre le navigateur une fois le serveur prêt.
if [[ -z "${NO_BROWSER:-}" ]]; then
    (
        for _ in $(seq 1 30); do
            curl -s -o /dev/null --max-time 2 "$url" && { open_browser; break; }
            sleep 0.5
        done
    ) &
fi

echo "→ Lancement du dashboard (Ctrl+C pour arrêter)"
exec "$PY" src/dashboard_clio.py "${args[@]}"
