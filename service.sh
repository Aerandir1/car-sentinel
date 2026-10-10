#!/usr/bin/env bash
# Enregistrement permanent : le script tourne en service systemd (utilisateur) et démarre avec la session.
# Il attend l'ELM327, enregistre dès que le calculateur répond, et reprend tout seul après une coupure.
#
#   ./service.sh install       # installe et démarre le service
#   ./service.sh status        # état et dernières lignes du journal
#   ./service.sh remove        # arrête et supprime le service
#
# Options du script : CLIO_ARGS="--port /dev/ttyUSB0" ./service.sh install
# Accès depuis le réseau : CLIO_ARGS="--lan" ./service.sh install   (voir README)

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

NAME=clio-sentinel
UNIT="$HOME/.config/systemd/user/$NAME.service"
VENV="${VENV:-$HOME/.venvs/obd}"

case "${1:-}" in
install)
    if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c "import obd" 2>/dev/null; then
        echo "→ Création du venv $VENV"
        python -m venv "$VENV"
        "$VENV/bin/python" -m pip install -q -r requirements.txt
    fi
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<EOF
[Unit]
Description=Clio Sentinel : enregistrement OBD permanent
After=network.target

[Service]
WorkingDirectory=$PWD
ExecStart=$VENV/bin/python src/dashboard_clio.py ${CLIO_ARGS:-}
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=CLIO_CERT_NAMES=${CLIO_CERT_NAMES:-}

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now "$NAME"
    echo "→ Service installé et démarré : https://clio-sentinel.localhost:8080"
    echo "  Pour qu'il démarre avec l'ordi même sans ouvrir de session : sudo loginctl enable-linger $USER"
    ;;
status)
    systemctl --user status "$NAME" --no-pager || true
    ;;
remove)
    systemctl --user disable --now "$NAME" || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "→ Service supprimé"
    ;;
*)
    sed -n 2,9p "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
