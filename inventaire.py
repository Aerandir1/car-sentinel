"""Inventaire des capteurs de la voiture (Clio Sentinel).

À lancer une fois dans la voiture, contact mis (puis moteur tournant pour un second relevé) :
  1. demande au calculateur tous les PID qu'il supporte en mode 01, y compris ceux que python-obd
     ne connaît pas (au-delà de 0x5F : FAP, EGR, turbo, couple…), lit chacun et chronomètre la réponse ;
  2. mesure la cadence réelle de l'ELM327 (un PID seul, puis plusieurs en alternance), en mode normal
     puis en mode rapide.

Résultat affiché et enregistré dans logs/inventaire-AAAAMMJJ-HHMM.json.

    python inventaire.py                         # ELM327 auto-détecté
    python inventaire.py --port /dev/ttyUSB0 --note "moteur chaud, ralenti"
    python inventaire.py --port socket://192.168.0.10:35000   # ELM327 Wi-Fi
"""

import argparse
import json
import logging
import signal
import statistics
import time
from datetime import datetime
from pathlib import Path

import obd
from obd import OBDCommand
from obd.protocols import ECU

HERE = Path(__file__).resolve().parent

# Noms SAE J1979 des PID mode 01 que python-obd ne décode pas (valeur donnée en brut)
EXTRA_NAMES = {
    0x61: "Couple demandé conducteur (%)", 0x62: "Couple réel moteur (%)", 0x63: "Couple de référence (Nm)",
    0x64: "Points de couple moteur", 0x65: "Entrées/sorties auxiliaires", 0x66: "Débitmètre d'air (MAF)",
    0x67: "Température d'eau (capteurs)", 0x68: "Température d'air d'admission (capteurs)",
    0x69: "EGR commandée / écart", 0x6A: "Commande débit d'air admission diesel", 0x6B: "Température EGR",
    0x6C: "Commande papillon", 0x6D: "Régulation pression carburant", 0x6E: "Régulation pression d'injection",
    0x6F: "Pression entrée compresseur turbo", 0x70: "Régulation pression de suralimentation",
    0x71: "Commande turbo à géométrie variable", 0x72: "Commande wastegate", 0x73: "Pression d'échappement",
    0x74: "Régime turbo", 0x75: "Température turbo A", 0x76: "Température turbo B",
    0x77: "Température air de suralimentation (échangeur)", 0x78: "Température gaz d'échappement banc 1",
    0x79: "Température gaz d'échappement banc 2", 0x7A: "FAP banc 1 (pression différentielle…)",
    0x7B: "FAP banc 2", 0x7C: "Température FAP", 0x7D: "Statut zone NTE NOx", 0x7E: "Statut zone NTE particules",
    0x7F: "Temps de fonctionnement moteur", 0x81: "Temps AECD 1", 0x82: "Temps AECD 2", 0x83: "Capteur NOx",
    0x84: "Température surface collecteur", 0x85: "Système réactif NOx", 0x86: "Capteur de particules",
    0x87: "Pression absolue collecteur d'admission", 0x88: "Système d'induction SCR", 0x8B: "Post-traitement diesel",
    0x8C: "Sonde O2 large bande", 0x8D: "Position papillon G", 0x8E: "Couple de frottement moteur (%)",
    0x8F: "Capteur de particules bancs 1 et 2", 0x94: "Alerte NOx", 0x98: "Température échappement banc 1 (capteurs)",
    0x99: "Température échappement banc 2 (capteurs)", 0x9C: "Données sonde O2", 0x9D: "Débit carburant moteur",
    0x9E: "Débit gaz d'échappement", 0x9F: "Utilisation système carburant", 0xA0: "Capteur NOx corrigé",
    0xA2: "Débit carburant par cylindre", 0xA3: "Pression vapeur EVAP", 0xA4: "Rapport de boîte engagé",
    0xA5: "Dosage DEF commandé", 0xA6: "Odomètre",
}

# PID rapides pour le test de cadence (régime, vitesse, pression admission, pédale)
CADENCE_PIDS = ["RPM", "SPEED", "INTAKE_PRESSURE", "ACCELERATOR_POS_D"]


class Stuck(Exception):
    """L'adaptateur ne répond plus du tout (certains clones se bloquent sur un PID inconnu)."""


def guarded(seconds, fn, *a):
    """Appelle fn en coupant au bout de `seconds` (SIGALRM : Linux / macOS / Docker)."""
    if not hasattr(signal, "SIGALRM"):
        return fn(*a)

    def stop(*_):
        raise Stuck()
    old = signal.signal(signal.SIGALRM, stop)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn(*a)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def raw(messages):
    """Décodeur brut : octets de données renvoyés par chaque calculateur."""
    return {f"{m.tx_id:X}" if m.tx_id is not None else "?": bytes(m.data).hex(" ").upper()
            for m in messages if m.data}


def raw_cmd(mode, pid):
    return OBDCommand(f"RAW_{mode:02X}{pid:02X}", "", f"{mode:02X}{pid:02X}".encode(), 0, raw, ECU.ALL, False)


def timed(conn, cmd, force=False):
    t = time.perf_counter()
    r = conn.query(cmd, force=force)
    return r, (time.perf_counter() - t) * 1000


def supported_pids(conn, mode=1):
    """PID supportés, lus sur les masques 00, 20, 40… de tous les calculateurs qui répondent."""
    pids, ecus, base = set(), set(), 0
    while base <= 0xE0:
        r = conn.query(raw_cmd(mode, base), force=True)
        if r.is_null() or not r.value:
            break
        mask = 0
        for ecu, data in r.value.items():
            b = bytes.fromhex(data.replace(" ", ""))
            if len(b) >= 6 and b[1] == base:
                ecus.add(ecu)
                mask |= int.from_bytes(b[2:6], "big")
        for i in range(32):
            if mask & (1 << (31 - i)):
                pids.add(base + i + 1)
        if not mask & 1:   # dernier bit : le bloc suivant existe-t-il ?
            break
        base += 0x20
    return sorted(p for p in pids if p % 0x20), sorted(ecus)


def read_pid(conn, pid, tries=3):
    """Lit un PID plusieurs fois : valeur décodée (si python-obd le connaît), brut, temps de réponse."""
    known = obd.commands.has_pid(1, pid)
    cmd = obd.commands[1][pid] if known else raw_cmd(1, pid)
    rawc = raw_cmd(1, pid)
    times, value = [], None
    for _ in range(tries):
        r, ms = timed(conn, cmd, force=True)
        if not r.is_null():
            times.append(ms)
            value = r.value
    rr = conn.query(rawc, force=True)
    if hasattr(value, "magnitude"):
        shown = f"{value.magnitude:.4g} {value.units}"
    elif isinstance(value, (str, int, float, tuple, list)):
        shown = str(value)
    else:   # objets python-obd sans affichage lisible : on s'en tient au brut
        shown = None
    return {
        "pid": f"{pid:02X}",
        "nom": cmd.name if known else f"PID_{pid:02X}",
        "description": cmd.desc if known else EXTRA_NAMES.get(pid, "PID constructeur / non répertorié"),
        "connu_python_obd": known,
        "valeur": shown,
        "brut": None if rr.is_null() else rr.value,
        "reponses": f"{len(times)}/{tries}",
        "ms_median": round(statistics.median(times)) if times else None,
    }


def cadence(conn, names, seconds):
    """Lectures réussies par seconde en tournant sur la liste de PID."""
    cmds = [obd.commands[n] for n in names]
    ok = total = 0
    end = time.perf_counter() + seconds
    while time.perf_counter() < end:
        r = conn.query(cmds[total % len(cmds)], force=True)
        total += 1
        ok += not r.is_null()
    return {"pids": names, "duree_s": seconds, "requetes": total, "reussies": ok,
            "hz_total": round(ok / seconds, 1), "hz_par_pid": round(ok / seconds / len(cmds), 2)}


def alive(conn):
    return conn.status() == obd.OBDStatus.CAR_CONNECTED


def connect(args, fast):
    conn = obd.OBD(portstr=args.port, baudrate=args.baud, fast=fast, timeout=args.timeout)
    if not conn.is_connected():
        raise SystemExit(f"Pas de connexion au calculateur ({conn.status()}). Contact mis ? Bon port ?")
    return conn


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", help="port de l'ELM327 (/dev/ttyUSB0, socket://IP:PORT…) ; auto si absent")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    p.add_argument("--timeout", type=float, default=1.0, help="attente max d'une réponse, en s (défaut 1)")
    p.add_argument("--duree", type=float, default=8.0, help="durée de chaque test de cadence, en s (défaut 8)")
    p.add_argument("--note", default="", help="contexte du relevé (ex. « moteur chaud, ralenti »)")
    p.add_argument("--sortie", help="fichier JSON (défaut logs/inventaire-AAAAMMJJ-HHMM.json)")
    args = p.parse_args()
    logging.getLogger("obd").setLevel(logging.CRITICAL)   # les erreurs sont gérées et affichées ici

    out = Path(args.sortie) if args.sortie else HERE / "logs" / f"inventaire-{datetime.now():%Y%m%d-%H%M}.json"
    report = {"date": datetime.now().isoformat(timespec="seconds"), "note": args.note}
    limit = args.timeout * 4 + 3   # au-delà, l'adaptateur est considéré comme bloqué

    print("→ Connexion à l'ELM327…")
    conn = connect(args, fast=False)
    try:
        elm = conn.query(obd.commands.ELM_VERSION, force=True).value
        volt = conn.query(obd.commands.ELM_VOLTAGE, force=True).value
        report.update({"port": conn.port_name(), "protocole": conn.protocol_name(), "elm": elm,
                       "tension_v": round(volt.magnitude, 2) if volt is not None else None})
        print(f"  {report['port']} · {report['protocole']} · {elm} · {report['tension_v']} V")

        print("→ Recherche des PID supportés (mode 01)…")
        pids, ecus = guarded(limit * 8, supported_pids, conn)
        report.update({"calculateurs": ecus, "capteurs": [], "cadence": {}})
        print(f"  {len(pids)} PID supportés, calculateur(s) qui répondent : {', '.join(ecus) or '?'}")

        for i, pid in enumerate(pids, 1):
            try:
                c = guarded(limit, read_pid, conn, pid)
            except Stuck:
                c = None
            if c is None or not alive(conn):
                # Ce PID a bloqué ou fait décrocher l'adaptateur : on le note et on se reconnecte
                c = {"pid": f"{pid:02X}", "nom": f"PID_{pid:02X}", "description": EXTRA_NAMES.get(pid, ""),
                     "erreur": "l'adaptateur a décroché sur ce PID"}
                print(f"  [{i:2}/{len(pids)}] {pid:02X} fait décrocher l'adaptateur → reconnexion")
                report["capteurs"].append(c)
                conn.close()
                conn = connect(args, fast=False)
                continue
            report["capteurs"].append(c)
            val = c["valeur"] or ", ".join(f"{k}: {v}" for k, v in (c["brut"] or {}).items()) or "—"
            ms = f"{c['ms_median']} ms" if c["ms_median"] is not None else "pas de réponse"
            print(f"  [{i:2}/{len(pids)}] {c['pid']} {c['description'][:42]:42} {val[:34]:34} {ms}")

        rpm = conn.query(obd.commands.RPM, force=True).value
        if rpm is not None and rpm.magnitude < 100:
            print("  (moteur arrêté : certaines valeurs sont à 0, refaire un relevé moteur tournant)")

        usable = [n for n in CADENCE_PIDS if obd.commands[n].pid in pids] or ["RPM"]
        for label, fast in (("normal", False), ("rapide", True)):
            if fast:
                conn.close()
                print("→ Reconnexion en mode rapide…")
                try:
                    conn = connect(args, fast=True)
                except SystemExit as e:
                    report["cadence"][label] = {"erreur": str(e)}
                    print(f"  {e}")
                    continue
            print(f"→ Cadence, mode {label} ({args.duree:g} s par test)…")
            try:
                seul = guarded(args.duree + limit, cadence, conn, ["RPM"], args.duree)
                multi = guarded(args.duree + limit, cadence, conn, usable, args.duree)
                if not alive(conn):
                    raise Stuck()
            except Stuck:
                report["cadence"][label] = {"erreur": "l'adaptateur a cessé de répondre"}
                print("  l'adaptateur a cessé de répondre")
                continue
            report["cadence"][label] = {"un_pid": seul, "plusieurs_pids": multi}
            print(f"  1 PID : {seul['hz_total']} lectures/s ({seul['reussies']}/{seul['requetes']} réussies)")
            print(f"  {len(usable)} PID en alternance : {multi['hz_total']} lectures/s au total, "
                  f"{multi['hz_par_pid']} par PID ({multi['reussies']}/{multi['requetes']} réussies)")
    except (Stuck, SystemExit, KeyboardInterrupt) as e:
        report["interrompu"] = str(e) or type(e).__name__
        print(f"  Relevé interrompu ({report['interrompu']}) : sauvegarde de ce qui a été lu.")
    finally:
        conn.close()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"→ Rapport enregistré : {out}")

if __name__ == "__main__":
    main()
