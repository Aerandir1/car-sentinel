#!/usr/bin/env python3
"""Test de l'ELM327 : quelles commandes AT la clé accepte vraiment, quel que soit le numéro de version qu'elle annonce.

Les clés bon marché sont des clones : ATI répond « ELM327 v1.5 » ou « v2.1 » au choix du fabricant, et certaines
commandes manquent (réponse « ? »). Le test envoie :

  1. les commandes AT dont car-sentinel se sert : si l'une est refusée, la fonction qui l'emploie ne marchera pas ;
  2. d'autres commandes, sans effet durable, qui disent si le firmware est complet (un vrai ELM327 les connaît) ;
  3. contact mis seulement : quelques requêtes OBD de lecture (mode 01), dont plusieurs PID par requête.

Rien n'est écrit dans la clé (pas d'ATPP, ATSD) ni dans la voiture. La clé est remise à ses réglages d'office à la fin.

    ./service.sh stop                          # le dashboard tient l'adaptateur pendant qu'il tourne
    ~/.venvs/obd/bin/python src/test_elm.py    # --port /dev/ttyACM0, --baud 38400
    ./service.sh start
"""

import argparse
import sys
import time

import acquisition
import config

# (commande, à quoi elle sert)
USED = (
    ("ATE0", "écho coupé"),
    ("ATL0", "sans saut de ligne"),
    ("ATS0", "réponses sans espaces"),
    ("ATH1", "en-têtes affichés"),
    ("ATSP0", "protocole automatique"),
    ("ATDPN", "numéro du protocole"),
    ("ATRV", "tension de la batterie"),
    ("ATST32", "délai d'attente 200 ms (scan des PID)"),
    ("ATST0A", "délai d'attente 40 ms (scan des PID)"),
    ("ATAT0", "délai d'attente fixe"),
    ("ATAT1", "délai d'attente adaptatif"),
    ("ATAT2", "délai d'attente adaptatif agressif"),
    ("ATSH7DF", "requête à tous les calculateurs"),
    ("ATSH7E0", "requête au calculateur moteur seul"),
    ("ATCRA7E8", "filtre des réponses (diagnostic)"),
    ("ATCRA", "filtre retiré"),
    ("ATSH81 10 F1", "en-tête KWP (diagnostic Renault / Ford ancien)"),
    ("ATWM81 10 F1 3E", "message de maintien KWP (diagnostic)"),
    ("ATIIA10", "adresse d'init ISO (diagnostic)"),
    ("ATPC", "fermeture du protocole"),
)
EXTRA = (
    ("AT@1", "description (un vrai ELM327 : « OBDII to RS232 Interpreter »)"),
    ("AT@2", "identifiant de l'appareil"),
    ("ATCS", "état du bus CAN"),
    ("ATKW", "octets clés ISO"),
    ("ATIGN", "état de la broche contact"),
    ("ATBD", "contenu du tampon"),
    ("ATRD", "octet stocké"),
    ("ATPPS", "liste des paramètres programmables"),
    ("ATCAF1", "formatage CAN automatique"),
    ("ATCFC1", "contrôle de flux CAN"),
    ("ATAMC", "compteur d'activité"),
    ("ATFE", "oubli des événements"),
    ("ATCSM1", "mode silencieux CAN"),
    ("STI", "puce STN (OBDLink) au lieu d'un clone PIC"),
)
# Lecture seule, mode 01 : PID supportés, puis 1 à 6 PID par requête (régime, vitesse, eau, admission, papillon, MAF)
OBD = (("0100", 1), ("010C", 1), ("010C0D", 2), ("010C0D05", 3), ("010C0D050F", 4), ("010C0D050F11", 5),
       ("010C0D050F1110", 6))


def send(s, cmd, timeout=2.0):
    """Réponse de la clé à cmd (texte sans écho ni invite), None sans réponse avant timeout."""
    s.reset_input_buffer()
    s.write(cmd.encode() + b"\r")
    reply, end = b"", time.time() + timeout
    while time.time() < end and not reply.endswith(b">"):
        reply += s.read(64)
    if not reply.endswith(b">"):
        return None
    lines = [x.strip() for x in reply[:-1].decode("ascii", "ignore").replace("\r", "\n").split("\n")]
    return " | ".join(x for x in lines if x and x.replace(" ", "") != cmd.replace(" ", ""))


def verdict(reply):
    if reply is None:
        return "muet"
    if reply.strip() == "?":
        return "REFUSÉ"
    return "ok"


def run(s, title, tests):
    """Envoie chaque commande, affiche le résultat ; renvoie les refusées."""
    print(f"\n{title}")
    refused = []
    for cmd, what in tests:
        reply = send(s, cmd)
        v = verdict(reply)
        if v != "ok":
            refused.append(cmd)
        shown = "" if reply in (None, "OK", "?") else f"  → {reply[:60]}"
        print(f"  {v:7} {cmd:16} {what}{shown}")
    return refused

SIZES = {"0C": 2, "0D": 1, "05": 1, "0F": 1, "11": 1, "10": 2}   # octets de données de chaque PID


def count_pids(cmd, reply):
    """PID de cmd présents dans la réponse : trames CAN (« 0:410C… ») recollées, puis lues PID par PID."""
    data = "".join(x.split(":", 1)[-1] for x in reply.split(" | ")).replace(" ", "")
    data = data[data.find("41") + 2:] if "41" in data else ""
    wanted, got = {cmd[i:i + 2] for i in range(2, len(cmd), 2)}, set()
    while len(data) >= 2 and data[:2] in SIZES:
        pid, data = data[:2], data[2:]
        if len(data) < 2 * SIZES[pid]:
            break
        got.add(pid)
        data = data[2 * SIZES[pid]:]
    return len(wanted & got)


def obd(s):
    """Requêtes OBD si le calculateur répond : nombre de PID rendus pour chaque groupe."""
    print("\n3. Requêtes OBD (contact mis)")
    volts = send(s, "ATRV") or ""
    if not any(c.isdigit() for c in volts):
        print(f"  pas de tension sur la prise OBD (ATRV : {volts or 'muet'}) : clé hors de la voiture ? Partie sautée")
        return None
    for c in ("ATH0", "ATST32", "ATAT1"):   # réglages laissés par les tests AT : pas d’en-têtes, 200 ms
        send(s, c)
    send(s, "ATSH7DF")
    first = send(s, "0100", timeout=8)   # le protocole automatique peut chercher plusieurs secondes
    if not first or "41" not in first:
        print(f"  calculateur muet ({first or 'pas de réponse'}) : contact coupé ? Partie sautée")
        return None
    print(f"  protocole : {send(s, 'ATDP')}")
    best = 0
    for cmd, n in OBD[1:]:
        reply = send(s, cmd, timeout=3) or ""
        got = count_pids(cmd, reply)
        print(f"  {'ok' if got == n else 'partiel':7} {cmd:16} {got}/{n} PID rendus  → {reply[:60]}")
        if got == n:
            best = n
    return best


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", help="port série de l'ELM327 (auto-détection si absent)")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    args, _ = p.parse_known_args(config.argv() + sys.argv[1:])   # ADAPTATEUR, DEBIT de car.conf

    import serial

    port, baud, fd = args.port, args.baud, -1
    if port:
        fd = acquisition.lock_port(port)
        if not baud:
            found = acquisition.probe(port)
            if not found or found == "occupe":
                sys.exit(f"Pas d'ELM327 qui réponde sur {port}")
            baud = found[0]
    else:
        port, found, name, fd, _ = acquisition.find_adapter("/dev")
        if port is None:
            denied, busy = found, name
            if busy:
                sys.exit(f"{busy[0]} est tenu par un autre programme : arrêter le dashboard (./service.sh stop)")
            sys.exit("Aucun ELM327 ne répond" + (f" ({denied[0]} : pas le droit de l'ouvrir)" if denied else ""))
        baud = baud or found
    if fd is None:
        sys.exit(f"{port} est tenu par un autre programme : arrêter le dashboard (./service.sh stop)")

    try:
        with serial.Serial(port, baud, timeout=0.2, write_timeout=1) as s:
            s.write(b"\r")
            time.sleep(0.2)
            reset = send(s, "ATZ", timeout=5)
            print(f"Adaptateur sur {port} à {baud} bauds : {reset or 'pas de réponse à ATZ'}")
            print(f"  ATI : {send(s, 'ATI')}")
            used = run(s, "1. Commandes dont car-sentinel se sert", USED)
            extra = run(s, "2. Autres commandes (firmware complet ?)", EXTRA)
            send(s, "ATCSM0")
            try:
                group = obd(s)
                send(s, "ATD")   # réglages d'office
                send(s, "ATPC")
            except serial.SerialException:
                group = None
                print("  la clé a quitté l'USB (redémarrage sur une chute de tension ?) : partie interrompue")
    finally:
        acquisition.release(fd)

    print("\nBilan")
    if used:
        print(f"  {len(used)} commande(s) utilisée(s) par car-sentinel refusée(s) : {', '.join(used)}")
    else:
        print("  toutes les commandes dont car-sentinel se sert sont acceptées")
    extra = [c for c in extra if c != "STI"]   # STI n'existe que sur les puces STN : refusée par tout ELM327
    print(f"  autres commandes refusées : {', '.join(extra) or 'aucune'}"
          + ("  (firmware incomplet : clone)" if extra else ""))
    if group:
        print(f"  jusqu'à {group} PID par requête rendus en entier")


if __name__ == "__main__":
    main()
