#!/usr/bin/env bash
# Enregistrement permanent : le script tourne en service systemd (utilisateur) et démarre avec la session.
# Il attend l'ELM327, enregistre dès que le calculateur répond, et reprend tout seul après une coupure.
#
#   ./service.sh install       # installe et démarre le service
#   ./service.sh restart       # relance après un changement dans clio.conf
#   ./service.sh status        # état et adresses de la page
#   ./service.sh remove        # arrête et supprime le service
#
# Les réglages (accès réseau, serveur, adaptateur…) sont dans clio.conf, créé au premier lancement.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

NAME=clio-sentinel
UNIT="$HOME/.config/systemd/user/$NAME.service"
VENV="${VENV:-$HOME/.venvs/obd}"

# Lignes utiles du journal depuis le dernier démarrage : adresses de la page, synchro, avertissements
addresses() {
    local since
    since=$(systemctl --user show -p ActiveEnterTimestamp --value "$NAME")
    [[ -n "$since" ]] || return 0
    journalctl --user -u "$NAME" --since "$since" -o cat --no-pager \
        | grep -E '^(Dashboard|  sur le réseau|  \(pas de mot|Synchro|Réglages|Attention)' | head -n 15 || true
}

case "${1:-}" in
install)
    if [[ ! -x "$VENV/bin/python" ]] || ! "$VENV/bin/python" -c "import obd" 2>/dev/null; then
        echo "→ Création du venv $VENV"
        python -m venv "$VENV"
        "$VENV/bin/python" -m pip install -q -r requirements.txt
    fi
    [[ -f clio.conf ]] || { cp clio.conf.exemple clio.conf; echo "→ Réglages créés dans $PWD/clio.conf"; }
    mkdir -p "$(dirname "$UNIT")"
    cat > "$UNIT" <<EOF
[Unit]
Description=Clio Sentinel : enregistrement OBD permanent
After=network-online.target

[Service]
WorkingDirectory=$PWD
ExecStart=$VENV/bin/python src/dashboard_clio.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable "$NAME"
    systemctl --user restart "$NAME"
    sleep 2
    echo "→ Service installé et démarré (réglages : $PWD/clio.conf)"
    addresses
    echo "  Pour qu'il démarre avec l'ordi même sans ouvrir de session : sudo loginctl enable-linger $USER"
    ;;
restart)
    systemctl --user restart "$NAME"
    sleep 2
    addresses
    ;;
status)
    systemctl --user status "$NAME" --no-pager --lines 0 || true
    echo
    addresses
    ;;
remove)
    systemctl --user disable --now "$NAME" || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "→ Service supprimé"
    ;;
*)
    sed -n 2,10p "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
