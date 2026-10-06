#!/usr/bin/env bash
# Lance le serveur du dashboard Clio.
#
#   ./lancer.sh                 # dans la voiture (ELM327 auto-détecté)
#   ./lancer.sh demo            # simulation, sans voiture
#   ./lancer.sh --port /dev/ttyUSB0 --log trajet.csv --gps /dev/ttyACM0
#   ./lancer.sh inventaire          # inventaire des capteurs (inventaire.py)
#
# Les options sont transmises telles quelles à dashboard_clio.py (voir README).
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

# Inventaire des capteurs : même environnement Python, sans serveur ni navigateur
if [[ "${1:-}" == "inventaire" ]]; then
    shift
    exec "$PY" inventaire.py "$@"
fi

# Raccourci « demo » → --demo
args=()
for a in "$@"; do
    [[ "$a" == "demo" ]] && args+=("--demo") || args+=("$a")
done

# URL de la page (suit --http et --no-https s'ils sont passés)
scheme=https; http_port=8080; prev=""
for a in "${args[@]}"; do
    [[ "$a" == "--no-https" ]] && scheme=http
    [[ "$prev" == "--http" ]] && http_port="$a"
    [[ "$a" == --http=* ]] && http_port="${a#--http=}"
    prev="$a"
done
url="$scheme://127.0.0.1:$http_port"

# Ouvre Chromium (WebUSB pour CarPlay) une fois le serveur prêt.
if [[ -z "${NO_BROWSER:-}" ]]; then
    (
        for _ in $(seq 1 30); do
            curl -sk -o /dev/null "$url" && break
            sleep 0.5
        done
        if command -v chromium >/dev/null; then
            chromium "$url" >/dev/null 2>&1 &
        else
            xdg-open "$url" >/dev/null 2>&1 &
        fi
    ) &
fi

echo "→ Dashboard sur $url  (Ctrl+C pour arrêter)"
exec "$PY" dashboard_clio.py "${args[@]}"
