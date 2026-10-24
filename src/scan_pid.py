#!/usr/bin/env python3
"""Scan des PID : temps de réponse et fiabilité de chaque mesure, et façon la plus rapide d'interroger le calculateur.

Chaque essai est répété (--tours, 20 par défaut) : une façon de demander n'est retenue que si elle répond à tous
les coups. Étapes :

  1. adaptateur seul      aller-retour avec l'ELM327 sans passer par la voiture (ATRV) : le plancher
  2. adressage            diffusion (7DF), calculateur moteur seul (7E0), avec ou sans nombre de réponses attendu
  3. délai d'attente      ATST / ATAT : combien de temps l'ELM327 attend après la dernière réponse (200 ms d'office)
  4. mesure par mesure    temps de réponse, part de réponses ; mesures muettes, instables ou lentes
  5. groupage             2 à 6 PID OBD (2 à 4 mesures Ford ou Renault) par requête : tout le groupe est-il rendu ?
  6. tour complet         la boucle du dashboard, sans puis avec les réglages trouvés : fréquence réelle

La voiture est reconnue au VIN, comme par le dashboard. Le résultat est écrit dans data/scan-pid-<voiture>.json :
le dashboard le reprend à la connexion suivante (requetes.py).
Contact mis, moteur tournant de préférence (certaines mesures ne répondent pas moteur coupé) :

    ./service.sh stop                         # le dashboard tient l'adaptateur pendant qu'il tourne
    ~/.venvs/obd/bin/python src/scan_pid.py   # --port /dev/ttyACM0, --tours 50, --voiture clio…
    ./service.sh start
"""

import argparse
import json
import logging
import sys
import time
from datetime import datetime

import acquisition
import config
import requetes
import voitures
from requetes import PERIOD

ST_STEPS = ("32", "19", "10", "0A", "06")   # ATST en pas de 4 ms : 200 (défaut), 100, 64, 40, 24 ms
TOUR_S = 15                                 # durée de chaque essai de la boucle complète
SLOW_FACTOR = 2                             # « lente » : plus de 2 fois le temps médian des mesures


class Collect:
    """Faux état du dashboard pour Reader.tick : compte les réponses de chaque mesure."""

    def __init__(self):
        self.n, self.null = {}, {}

    def update(self, key, value):
        self.n[key] = self.n.get(key, 0) + 1
        self.null[key] = self.null.get(key, 0) + (value is None)

    def update_raw(self, key, raw):
        self.update(key, raw)


def summary(ms):
    """(médiane, 90e centile) en ms, ou (None, None)."""
    if not ms:
        return None, None
    s = sorted(ms)
    return s[len(s) // 2], s[int(0.9 * (len(s) - 1))]


def fmt(v, unit=" ms"):
    return "—" if v is None else f"{v:.0f}{unit}"


class Scan:
    def __init__(self, conn, cmds, car, tours):
        self.conn, self.elm, self.cmds, self.car, self.tours = conn, conn.interface, cmds, car, tours
        self.reader = requetes.Reader(conn, cmds, car)
        self.partial = []   # taille des réponses partielles (groupes)
        self.reader._shrink = lambda uds, n: self.partial.append(n)   # le scan note, sans réduire

    def at(self, cmd):
        """Commande AT ; False si l'adaptateur la refuse (« ? »)."""
        lines = self.elm._ELM327__send(cmd.encode())   # réponse brute : send_and_parse cache le « ? »
        return not any("?" in line for line in lines)

    def run(self, group, count, tours=None):
        """Lit le groupe `tours` fois : (temps de chaque réponse complète en ms, part des réponses complètes)."""
        ms, ok = [], 0
        for _ in range(tours or self.tours):
            t0 = time.perf_counter()
            values = self.reader._read(group, count)
            dt = (time.perf_counter() - t0) * 1000
            if values is not None and all(v is not None for v in values.values()):
                ok += 1
                ms.append(dt)
        return ms, ok / (tours or self.tours)

    # --- Étapes ---

    def adapter(self):
        ms = []
        for _ in range(self.tours):
            t0 = time.perf_counter()
            self.elm._ELM327__send(b"ATRV")
            ms.append((time.perf_counter() - t0) * 1000)
        med, p90 = summary(ms)
        print(f"  ATRV : {fmt(med)} (90 % sous {fmt(p90)}) : temps minimal d'une requête avec cet adaptateur")
        return med

    def addressing(self, probe):
        """Diffusion ou calculateur moteur seul, avec ou sans suffixe : renvoie (7E0 ?, suffixe ?)."""
        results = []
        for header, count, label in ((b"ATSH7DF", False, "diffusion 7DF"),
                                     (b"ATSH7E0", False, "moteur seul 7E0"),
                                     (b"ATSH7E0", True, "7E0 + nombre de réponses")):
            self.reader._send_at(header)
            self.reader.count_ok = count
            self.run(probe, count, 1)   # première réponse : nombre de trames appris par le Reader
            ms, ok = self.run(probe, count)
            med, p90 = summary(ms)
            print(f"  {label:<26} {fmt(med):>7}   90 % sous {fmt(p90):>7}   réponses {ok:4.0%}")
            results.append((ok, med, header, count))
        best = min((r for r in results if r[0] == 1), key=lambda r: r[1], default=results[0])
        self.reader._send_at(best[2])
        self.reader.count_ok = best[3]
        return best[2] == b"ATSH7E0", best[3]

    def timeout(self, probes):
        """Délai d'attente le plus court qui répond à tous les coups, sans suffixe (cas des réponses en
        plusieurs trames et des mesures muettes). Renvoie la liste de commandes AT à garder."""
        configs = [["ATAT1", "ATST32"], ["ATAT2", "ATST32"]] + [["ATAT0", "ATST" + st] for st in ST_STEPS]
        results = []
        for cfg in configs:
            if not all(self.at(c) for c in cfg):
                print(f"  {' '.join(cfg):<14} refusé par l'adaptateur")
                continue
            total, worst = [], 1.0
            for probe in probes:
                ms, ok = self.run(probe, False)
                total += ms
                worst = min(worst, ok)
            med, p90 = summary(total)
            print(f"  {' '.join(cfg):<14} {fmt(med):>7}   90 % sous {fmt(p90):>7}   réponses {worst:4.0%}")
            results.append((cfg, worst, med))
        self.at("ATAT1")
        self.at("ATST32")
        good = [r for r in results if r[1] == 1 and r[2] is not None]
        if not good:
            return []
        best = min(good, key=lambda r: r[2])
        cfg = best[0]
        if cfg[0] == "ATAT0":
            # ATST fixe : un cran de marge au-dessus du plus court qui a tout rendu
            i = ST_STEPS.index(cfg[1][4:])
            cfg = ["ATAT0", "ATST" + ST_STEPS[max(0, i - 1)]]
        if cfg == ["ATAT1", "ATST32"]:
            return []   # réglage d'office : rien à changer
        for c in cfg:
            self.at(c)
        return cfg

    def measures(self):
        out = {}
        for it in self.reader.items:
            ms, ok = self.run([it], None)
            med, p90 = summary(ms)
            out[it[0]] = {"ms": med and round(med, 1), "p90": p90 and round(p90, 1), "ok": round(ok, 3),
                          "maker": it[3]}
        meds = [m["ms"] for m in out.values() if m["ms"]]
        typical = summary(meds)[0] or 0
        for key, m in out.items():
            flag = ("muette" if m["ok"] == 0 else "instable" if m["ok"] < 0.9 else
                    "lente" if m["ms"] and m["ms"] > SLOW_FACTOR * typical else "")
            m["etat"] = flag or "ok"
            src = f"{self.car.MAKER[:4]:<4}" if m["maker"] else "OBD "
            print(f"  {key:<10} {src} {fmt(m['ms']):>7}   90 % sous {fmt(m['p90']):>7}   réponses {m['ok']:4.0%}"
                  + (f"   ← {flag}" if flag else ""))
        return out

    def grouping(self, uds, measures, limit):
        """Plus grand groupe rendu en entier à tous les coups ; les mesures fiables les plus fréquentes d'abord."""
        items = [it for it in self.reader.items if it[3] == uds and measures[it[0]]["ok"] == 1]
        items.sort(key=lambda it: it[4])
        best = 1
        for k in range(2, min(limit, len(items)) + 1):
            group = items[:k]
            self.partial.clear()
            self.run(group, None, 1)
            ms, ok = self.run(group, None)
            med, _ = summary(ms)
            per = med / k if med else None
            got = f"   (réponses partielles : {min(self.partial)} à {max(self.partial)} sur {k})" if self.partial else ""
            print(f"  {k} {'mesures' if uds else 'PID':<7} {fmt(med):>7}   soit {fmt(per):>6} par mesure"
                  f"   complètes {ok:4.0%}{got}")
            if ok < 1:
                break
            best = k
        if len(items) < 2:
            print("  moins de deux mesures fiables : rien à grouper")
        return best

    def loop(self, scan):
        """Boucle du dashboard pendant TOUR_S : (fréquence des tours, intervalle moyen par mesure, part de vides)."""
        self.at("ATAT1")
        self.at("ATST32")
        reader = requetes.Reader(self.conn, self.cmds, self.car, scan)
        notes = reader.setup()
        state, ticks, end = Collect(), 0, time.time() + TOUR_S
        while time.time() < end:
            reader.tick(state)
            ticks += 1
        hz = ticks / TOUR_S
        empty = sum(state.null.values()) / max(1, sum(state.n.values()))
        every = {k: TOUR_S / n for k, n in state.n.items()}
        fast = [every[k] for k in every if PERIOD.get(k) == 0]
        print(f"  {'avec le scan' if scan else 'sans le scan'} : {hz:.1f} tours/s, régime/vitesse lus toutes les "
              f"{fmt(1000 * min(fast) if fast else None)}, réponses vides {empty:.0%}"
              + (f"   ({', '.join(notes)})" if notes else ""))
        return {"hz": round(hz, 2), "vides": round(empty, 3)}


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--port", help="port série de l'ELM327 (auto-détection si absent)")
    p.add_argument("--baud", type=int, default=None, help="débit série (auto si absent)")
    p.add_argument("--tours", type=int, default=20, help="répétitions de chaque essai (défaut 20)")
    p.add_argument("--voiture", choices=sorted(voitures.CARS),
                   help="voiture, si le VIN ne la reconnaît pas (défaut : reconnue au VIN)")
    p.add_argument("--sortie", help="fichier du résultat (défaut data/scan-pid-<voiture>.json)")
    args, _ = p.parse_known_args(config.argv() + sys.argv[1:])   # ADAPTATEUR, DEBIT de car.conf

    obd = acquisition.import_obd()
    logging.getLogger("obd").setLevel(logging.CRITICAL)   # « Failed to read port »… : le scan dit l'essentiel

    port, baud, fd = args.port, args.baud, -1
    if port:
        fd = acquisition.lock_port(port)
    else:
        port, found, name, fd, _ = acquisition.find_adapter("/dev")
        if port is None:
            denied, busy = found, name
            if busy:
                sys.exit(f"{busy[0]} est tenu par un autre programme : arrêter le dashboard (./service.sh stop)")
            sys.exit("Aucun ELM327 ne répond" + (f" ({denied[0]} : pas le droit de l'ouvrir)" if denied else ""))
        baud = baud or found
        print(f"Adaptateur : {name} sur {port}")
    if fd is None:
        sys.exit(f"{port} est tenu par un autre programme : arrêter le dashboard (./service.sh stop)")

    try:
        conn = obd.OBD(portstr=port, baudrate=baud, fast=False, timeout=1)
        if conn.status() != obd.OBDStatus.CAR_CONNECTED:
            sys.exit(f"Pas de réponse du calculateur ({conn.status()}) : contact mis ?")
        print(f"Protocole : {conn.protocol_name()}")
        vin = acquisition.read_vin(obd, conn)
        found = voitures.detect(vin, acquisition.read_fuel_type(obd, conn))
        if not (found or args.voiture):
            sys.exit(f"Voiture non reconnue (VIN {vin or 'non donné'}) : la donner avec --voiture "
                     + " ou ".join(sorted(voitures.CARS)))
        car = voitures.get(found or args.voiture)
        args.sortie = args.sortie or str(requetes.scan_file(car))
        cmds, missing = acquisition.find_measures(obd, conn, car)
        print(f"{car.NAME} · VIN : {vin or 'non donné'} · {len(cmds)} mesures lues"
              + (f", sans réponse : {', '.join(missing)}" if missing else "") + f" · {args.tours} essais par test\n")
        scan = Scan(conn, cmds, car, args.tours)
        obd_items = [it for it in scan.reader.items if not it[3]]
        uds_items = [it for it in scan.reader.items if it[3]]
        probe = [it for it in obd_items if it[0] == "rpm"][:1] or obd_items[:1]

        print("1. Adaptateur seul")
        base = scan.adapter()
        print("\n2. Adressage (régime)")
        addr_7e0, count = scan.addressing(probe)
        print("\n3. Délai d'attente sans nombre de réponses (régime"
              + (f", mesure {car.MAKER})" if uds_items else ")"))
        at = scan.timeout([probe, uds_items[:1]] if uds_items else [probe])
        print("\n4. Mesure par mesure")
        measures = scan.measures()
        print("\n5. Groupage OBD (01)")
        max_obd = scan.grouping(False, measures, requetes.MAX_OBD)
        max_uds = 1
        if uds_items:
            print(f"\n   Groupage {car.MAKER} (22)")
            max_uds = scan.grouping(True, measures, requetes.MAX_UDS)

        result = {"date": datetime.now().isoformat(timespec="seconds"), "voiture": car.ID, "vin": vin,
                  "protocole": conn.protocol_name(), "tours": args.tours, "adaptateur_ms": round(base, 1),
                  "adressage_7e0": addr_7e0, "nombre_reponses": count, "at": at,
                  "max_obd": max_obd, "max_uds": max_uds, "mesures": measures}
        print("\n6. Tour complet, comme le dashboard")
        result["boucle_avant"] = scan.loop(None)
        result["boucle_apres"] = scan.loop(result)
        conn.close()
    finally:
        acquisition.release(fd)

    with open(args.sortie, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
        f.write("\n")
    bad = [k for k, m in measures.items() if m["etat"] != "ok"]
    watch = ", ".join(f"{k} ({measures[k]['etat']})" for k in bad)
    print(f"\nRetenu : {' '.join(at) or 'délai d’attente d’office'}, {max_obd} PID OBD et {max_uds} mesure{'s' if max_uds > 1 else ''} {car.MAKER}"
          " par requête" + (f" ; à surveiller : {watch}" if watch else ""))
    print(f"Écrit dans {args.sortie} : repris par le dashboard à la prochaine connexion (./service.sh start)")


if __name__ == "__main__":
    main()
