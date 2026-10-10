#!/usr/bin/env bash
# Enregistrement permanent : le dashboard tourne en service systemd (utilisateur) et démarre avec l'ordi.
# Il attend l'ELM327, enregistre dès que le calculateur répond, et reprend tout seul après une coupure.
#
#   ./service.sh install    # installe et démarre le service (une seule fois)
#   ./service.sh restart    # relance, après un changement dans clio.conf
#   ./service.sh status     # tourne-t-il ? adresses de la page, ou l'erreur qui l'empêche de démarrer
#   ./service.sh logs       # journal en direct (Ctrl+C pour quitter)
#   ./service.sh stop       # arrête jusqu'au prochain démarrage de l'ordi (start pour relancer)
#   ./service.sh remove     # arrête et supprime le service
#
# Les réglages (accès réseau, serveur, adaptateur…) sont dans clio.conf, créé au premier lancement.

set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

NAME=clio-sentinel
UNIT="$HOME/.config/systemd/user/$NAME.service"
VENV="${VENV:-$HOME/.venvs/obd}"

port() {   # PORT_PAGE de clio.conf, 8080 par défaut
    local p
    p=$(sed -n 's/^[[:space:]]*PORT_PAGE[[:space:]]*=[[:space:]]*\([0-9]\+\).*/\1/p' clio.conf 2>/dev/null | tail -n 1)
    echo "${p:-8080}"
}

# Journal depuis le dernier démarrage du service, sans les messages répétés de python-obd
journal() {
    local since
    since=$(systemctl --user show -p ExecMainStartTimestamp --value "$NAME" 2>/dev/null)
    journalctl --user -u "$NAME" ${since:+--since "$since"} -o cat --no-pager 2>/dev/null \
        | grep -v -E '^\[obd\.|^(Started|Stopped|Stopping) |clio-sentinel.service:' || true
}

# La page répond-elle ? Oui : adresses et avertissements. Non : l'erreur.
check() {
    local url="http://127.0.0.1:$(port)/api/state"
    for _ in $(seq 1 20); do
        if curl -sf --max-time 1 "$url" 2>/dev/null | grep -q '"status"'; then   # c'est bien le dashboard
            echo "✓ Dashboard en route"
            journal | grep -E '^(Dashboard|  sur le réseau|  \(pas de mot|Synchro|Attention|Réglages)' | head -n 15 || true
            return 0
        fi
        systemctl --user is-active -q "$NAME" || [[ "$(systemctl --user show -p ActiveState --value "$NAME")" == activating ]] || break
        sleep 0.5
    done
    echo "✗ Le dashboard ne démarre pas (./service.sh logs pour le journal complet) :"
    local recent errors
    recent=$(journalctl --user -u "$NAME" -n 80 -o cat --no-pager 2>/dev/null \
        | grep -v -E '^\[obd\.|^(Started|Stopped|Stopping) |clio-sentinel.service:' | tac | awk '!seen[$0]++' | tac)
    errors=$(grep -i -E 'erreur|error|exception|invalide|impossible' <<<"$recent" | tail -n 5 || true)
    sed 's/^/    /' <<<"${errors:-$(tail -n 8 <<<"$recent")}"
    return 1
}

need_unit() {
    [[ -f "$UNIT" ]] || { echo "Service pas installé : ./service.sh install"; exit 1; }
}

case "${1:-}" in
install)
    if ! "$VENV/bin/python" -c "import obd" 2>/dev/null; then
        echo "→ Installation de Python et des dépendances dans $VENV"
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
    systemctl --user enable -q "$NAME"
    systemctl --user restart "$NAME"
    echo "→ Service installé (réglages : $PWD/clio.conf)"
    check || true
    if [[ "$(loginctl show-user "$USER" -p Linger --value 2>/dev/null)" != yes ]]; then
        echo "  Pour qu'il démarre avec l'ordi même sans ouvrir de session : sudo loginctl enable-linger $USER"
    fi
    ;;
start|restart)
    need_unit
    systemctl --user restart "$NAME"
    check
    ;;
status)
    need_unit
    check
    ;;
logs)
    need_unit
    journalctl --user -u "$NAME" -f -o cat
    ;;
stop)
    need_unit
    systemctl --user stop "$NAME"
    echo "→ Arrêté (./service.sh start pour relancer ; il redémarrera aussi avec l'ordi)"
    ;;
remove)
    systemctl --user disable --now -q "$NAME" 2>/dev/null || true
    rm -f "$UNIT"
    systemctl --user daemon-reload
    echo "→ Service supprimé"
    ;;
*)
    sed -n 2,12p "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
