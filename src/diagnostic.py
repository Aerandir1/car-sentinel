"""Onglet Diagnostic : codes défaut et scan UDS des calculateurs, en lecture seule.

Le dashboard laisse l'ELM327 le temps du diagnostic (l'acquisition s'arrête, voir acquisition.py), le reprend
ensuite et l'enregistrement repart. Le diagnostic parle à l'adaptateur directement (commandes AT et requêtes
brutes), sans python-obd, pour pouvoir viser un calculateur précis (tableau de bord 743…).

Liste blanche (ALLOWED) vérifiée dans Elm.cmd, le seul endroit qui écrit sur le port : seules des lectures partent
vers la voiture, plus l'entrée / sortie de la session de diagnostic étendue (10 03 / 10 01) et son maintien (3E).
Tout le reste est refusé avant l'envoi : 10 02 (programmation), 27 (déverrouillage), 2E (écriture), 2F
(actionneurs), 31 (routines), 11 (redémarrage), 14 / 04 (effacement des défauts), 28, 85…
Même liste que clio-iv-obd-utils (obd_scan.py) : les deux doivent rester identiques.

Session étendue : moteur coupé et voiture à l'arrêt obligatoires (régime et vitesse lus juste avant), retour en
session normale à la fin, même sur erreur ou arrêt demandé.

Scans rangés dans data/diag/*.json, au format de uds_scan.py (python uds_scan.py --compare data/diag/*.json).
"""

import json
import re
import threading
import time
from pathlib import Path

import acquisition
import renault
from etat import METRICS

ALLOWED = [
    (r"AT.*", "réglage de l'adaptateur (ne part pas vers la voiture)"),
    (r"01[0-9A-F]{2}", "OBD 01 : valeur temps réel"),
    (r"09[0-9A-F]{2}", "OBD 09 : infos véhicule (VIN…)"),
    (r"0[37]", "OBD 03 / 07 : lecture des codes défaut"),
    (r"22[0-9A-F]{4}", "UDS 22 : lecture d'un identifiant"),
    (r"1902[0-9A-F]{2}", "UDS 19 02 : lecture des codes défaut"),
    (r"100[13]", "UDS 10 01 / 10 03 : session normale / étendue"),
    (r"3E[08]0", "UDS 3E : maintien de la session"),
]
_ALLOWED_RE = re.compile("|".join(f"(?:{p})" for p, _ in ALLOWED))

# Calculateurs proposés sur la page, avec les plages de DID fouillées par défaut
ECUS = {
    "7E0": ("Moteur", "2000-2FFF,F400-F4FF,FD00-FDFF"),
    "743": ("Tableau de bord", "0200-02FF,2000-21FF"),
}
MAX_DIDS = 16384
KEEPALIVE_S = 2.0   # session étendue : « 3E 80 » si rien n'est parti depuis ce temps (le calculateur lâche à ~5 s)
FREE_TIMEOUT_S = 30  # attente que l'acquisition laisse l'adaptateur
NRC = {0x11: "service non supporté", 0x12: "sous-fonction non supportée", 0x13: "longueur invalide",
       0x22: "conditions non remplies", 0x31: "identifiant inconnu", 0x33: "accès refusé (sécurité)",
       0x7E: "non supporté dans cette session", 0x7F: "service non supporté dans cette session"}
# Noms des DID déjà connus du dashboard (renault.py) : affichés à côté de la valeur brute
LABELS = {k: lbl for k, lbl, *_ in METRICS}
KNOWN_7E0 = {int(did, 16): (LABELS.get(key, key), gain, offset)
             for key, (did, gain, offset, *_r) in renault.FALLBACK.items()}
KNOWN_7E0[0xF40C] = ("Régime", 0.25, 0.0)


class Forbidden(Exception):
    """Requête hors de la liste blanche : jamais envoyée."""


class DiagError(Exception):
    pass


def check_allowed(command):
    if not _ALLOWED_RE.fullmatch(command.replace(" ", "").upper()):
        raise Forbidden(f"requête {command!r} refusée : hors de la liste blanche (lecture seule)")


def reply_addr(ecu):
    """Adresse de réponse CAN 11 bits : 7E0 → 7E8 (OBD), sinon +0x20 chez Renault (743 → 763)."""
    n = int(ecu, 16)
    return f"{n + 8:03X}" if 0x7E0 <= n <= 0x7E7 else f"{n + 0x20:03X}"


def parse_ranges(text):
    dids = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        lo, hi = int(lo, 16), int(hi or lo, 16)
        if not (0 <= lo <= hi <= 0xFFFF):
            raise ValueError(f"plage invalide : {part}")
        dids += range(lo, hi + 1)
    dids = list(dict.fromkeys(dids))
    if not dids:
        raise ValueError("aucune plage")
    if len(dids) > MAX_DIDS:
        raise ValueError(f"{len(dids)} DID : {MAX_DIDS} au plus par scan")
    return dids


def reassemble(chunks):
    """Réassemblage ISO-TP (trame simple ou multi-trames)."""
    first = [c for c in chunks if c and (c[0] >> 4) in (0, 1)]
    if not first:
        return b""
    ff = first[0]
    if ff[0] >> 4 == 0:
        return ff[1:1 + (ff[0] & 0x0F)]
    length = ((ff[0] & 0x0F) << 8) | ff[1]
    data = bytearray(ff[2:])
    for c in sorted((c for c in chunks if c and c[0] >> 4 == 2), key=lambda c: c[0] & 0x0F):
        data += c[1:]
    return bytes(data[:length])


def dtc_name(b0, b1):
    """Deux octets → code SAE, ex. 04 01 → P0401."""
    return "PCBU"[b0 >> 6] + str((b0 >> 4) & 3) + f"{b0 & 0xF:X}{b1:02X}"


class Elm:
    """ELM327 en commandes brutes. Toute requête passe par cmd(), qui applique la liste blanche."""

    def __init__(self, port, baud, timeout=2.0):
        import serial
        self.timeout = timeout
        self.ser = serial.serial_for_url(port, baudrate=baud or 38400, timeout=0.2)

    def cmd(self, command, timeout=None):
        check_allowed(command)
        self.ser.reset_input_buffer()
        self.ser.write((command + "\r").encode("ascii"))
        buf, end = b"", time.time() + (timeout or self.timeout)
        while time.time() < end and b">" not in buf:
            buf += self.ser.read(self.ser.in_waiting or 1)
        text = buf.decode("ascii", "ignore").replace(">", "")
        return [x.strip() for x in text.replace("\r", "\n").split("\n") if x.strip() and x.strip() != command]

    def frames(self, command, timeout=None):
        """Réponse par calculateur : {en-tête: données réassemblées} (CAN 11 bits, en-têtes affichés)."""
        by = {}
        for line in self.cmd(command, timeout):
            line = line.replace(" ", "").upper()
            if len(line) < 5 or not re.fullmatch(r"[0-9A-F]+", line):
                continue   # NO DATA, SEARCHING…, ?
            try:
                by.setdefault(line[:3], []).append(bytes.fromhex(line[3:]))
            except ValueError:
                pass
        return {h: reassemble(c) for h, c in by.items()}

    def initialize(self):
        for c, t in (("ATZ", 3), ("ATE0", None), ("ATL0", None), ("ATS0", None), ("ATH1", None),
                     ("ATAT1", None), ("ATSP0", None)):
            self.cmd(c, t)
        resp = self.cmd("0100", timeout=20)   # lance la recherche du protocole
        if not any("4100" in x.replace(" ", "") for x in resp):
            raise DiagError("pas de réponse de la voiture (contact mis ?)")
        num = self.cmd("ATDPN")
        proto = num[0].lstrip("A") if num else "?"
        if proto not in ("6", "8"):
            raise DiagError(f"protocole {proto} : le diagnostic ne gère que le CAN 11 bits")

    def close(self):
        try:
            self.ser.close()
        except OSError:
            pass


class Uds:
    """Requêtes UDS adressées à un calculateur."""

    def __init__(self, elm, ecu):
        self.elm, self.ecu, self.rx = elm, ecu, reply_addr(ecu)
        self.extended, self.last = False, time.time()
        elm.cmd(f"ATSH{ecu}")
        elm.cmd(f"ATCRA{self.rx}")
        elm.cmd("ATST32")   # attente max ≈ 200 ms par requête

    def request(self, payload, timeout=2):
        if self.extended and payload != "3E80" and time.time() - self.last > KEEPALIVE_S:
            self.request("3E80", timeout=0.3)
        self.last = time.time()
        return self.elm.frames(payload, timeout).get(self.rx, b"")

    def read(self, did):
        """(True, données) si le DID répond, (False, code NRC) s'il est refusé, (None, None) sans réponse."""
        d = self.request(f"22{did:04X}")
        if len(d) >= 3 and d[0] == 0x62 and int.from_bytes(d[1:3], "big") == did:
            return True, d[3:]
        if len(d) >= 3 and d[0] == 0x7F and d[1] == 0x22:
            return False, d[2]
        return None, None

    def open_extended(self):
        d = self.request("1003")
        if len(d) >= 2 and d[0] == 0x50 and d[1] == 0x03:
            self.extended = True
            return
        if len(d) >= 3 and d[0] == 0x7F:
            raise DiagError(f"session étendue refusée par le calculateur : {NRC.get(d[2], f'code {d[2]:02X}')}")
        raise DiagError("session étendue : pas de réponse du calculateur")

    def close_session(self):
        """Retour en session normale (10 01) ; 2 essais (une réponse en retard peut être lue à la place)."""
        if not self.extended:
            return True
        self.extended = False
        for _ in range(2):
            d = self.request("1001")
            if len(d) >= 2 and d[0] == 0x50 and d[1] == 0x01:
                return True
        return False


def engine_stopped(elm):
    """Lève DiagError si le moteur tourne ou si la voiture roule (requête à tous les calculateurs, 7DF)."""
    elm.cmd("ATSH7DF")
    elm.cmd("ATCRA")   # écoute de toutes les réponses
    rpm = [int.from_bytes(d[2:4], "big") / 4 for d in elm.frames("010C").values() if len(d) >= 4 and d[0] == 0x41]
    speed = [d[2] for d in elm.frames("010D").values() if len(d) >= 3 and d[0] == 0x41]
    if not rpm:
        raise DiagError("régime illisible : impossible de vérifier que le moteur est coupé")
    if max(rpm) > 0:
        raise DiagError(f"moteur tournant ({max(rpm):.0f} tr/min) : couper le moteur, laisser le contact mis")
    if speed and max(speed) > 0:
        raise DiagError(f"voiture en mouvement ({max(speed)} km/h)")


class Diag:
    """Un diagnostic à la fois, dans un thread ; état lisible par la page (status)."""

    def __init__(self, state, args, data_dir):
        self.state, self.args = state, args
        self.dir = Path(data_dir) / "diag"
        self.lock = threading.Lock()
        self.stop_flag = threading.Event()
        self.job = None   # état du diagnostic en cours ou du dernier fini

    # ---- page ----
    def status(self):
        with self.lock:
            job = dict(self.job) if self.job else None
        return {"job": job, "scans": self.list_scans(),
                "ecus": {k: {"name": n, "ranges": r} for k, (n, r) in ECUS.items()},
                "allowed": [d for _p, d in ALLOWED]}

    def start(self, req):
        """Lance un diagnostic : {"job": "dtc"} ou {"job": "scan", "ecu", "ranges", "extended"}. ValueError si refusé."""
        if self.state.get_settings().get("demo"):
            raise ValueError("arrêter le mode démo d'abord (onglet Paramètres)")
        kind = req.get("job")
        if kind == "scan":
            ecu = str(req.get("ecu", "")).upper()
            if not re.fullmatch(r"[0-7][0-9A-F]{2}", ecu) or ecu == "7DF":
                raise ValueError("adresse de calculateur invalide (3 chiffres hexa, ex. 743)")
            dids = parse_ranges(str(req.get("ranges") or ECUS.get(ecu, ("", "2000-2FFF"))[1]))
            job = {"kind": "scan", "ecu": ecu, "extended": bool(req.get("extended")), "dids": dids}
        elif kind == "dtc":
            job = {"kind": "dtc"}
        else:
            raise ValueError("diagnostic inconnu")
        with self.lock:
            if self.job and self.job.get("running"):
                raise ValueError("un diagnostic est déjà en cours")
            self.stop_flag.clear()
            self.job = {"kind": kind, "running": True, "phase": "attente de l'adaptateur", "started": time.time(),
                        "ecu": job.get("ecu"), "extended": job.get("extended", False),
                        "done": 0, "total": len(job.get("dids", [])), "found": 0, "error": None,
                        "result": None, "file": None}
        threading.Thread(target=self._run, args=(job,), daemon=True).start()

    def stop(self):
        self.stop_flag.set()

    def _set(self, **kw):
        with self.lock:
            self.job.update(kw)

    # ---- déroulement ----
    def _run(self, job):
        st = self.state
        elm, fd = None, None
        st.diag_want.set()   # l'acquisition ferme sa connexion et laisse le port (acquisition.py)
        try:
            if not st.port_free.wait(FREE_TIMEOUT_S):
                raise DiagError("l'acquisition n'a pas laissé l'adaptateur")
            port, baud = self.args.port, self.args.baud
            if not port:
                port, found, name, fd = acquisition.find_adapter(self.args.dev)
                if not port:
                    raise DiagError("aucun ELM327 ne répond")
                baud = baud or found
            else:
                fd = acquisition.lock_port(port)
                if fd is None:
                    raise DiagError(f"{port} est utilisé par un autre programme")
            self._set(phase="connexion à la voiture")
            elm = Elm(port, baud)
            elm.initialize()
            if job["kind"] == "dtc":
                self._dtc(elm)
            else:
                self._scan(elm, job)
        except (DiagError, Forbidden, ValueError, OSError) as e:
            self._set(error=str(e))
        except Exception as e:   # erreur imprévue : notée, le dashboard reprend quand même la main
            import traceback
            traceback.print_exc()
            self._set(error=f"erreur imprévue : {e}")
        finally:
            if elm:
                elm.close()
            acquisition.release(fd)
            self._set(running=False, phase="terminé", ended=time.time())
            st.diag_want.clear()   # l'acquisition reprend l'adaptateur

    def _dtc(self, elm):
        """Codes défaut : OBD 03 (mémorisés) et 07 (en attente) à tous les calculateurs, puis UDS 19 02 sur le
        moteur et le tableau de bord. Rien n'est effacé."""
        out = []
        self._set(phase="codes défaut OBD (03 / 07)")
        elm.cmd("ATSH7DF")
        elm.cmd("ATCRA")
        for mode, kind in (("03", "mémorisé"), ("07", "en attente")):
            for hdr, d in elm.frames(mode, timeout=3).items():
                if len(d) >= 2 and d[0] == 0x40 + int(mode, 16):
                    for i in range(2, 2 + 2 * d[1], 2):
                        if i + 1 < len(d) and (d[i] or d[i + 1]):
                            out.append({"source": f"OBD {mode} · {hdr}", "code": dtc_name(d[i], d[i + 1]), "state": kind})
        for ecu, (name, _r) in ECUS.items():
            if self.stop_flag.is_set():
                break
            self._set(phase=f"codes défaut UDS · {name} ({ecu})")
            uds = Uds(elm, ecu)
            d = uds.request("1902FF", timeout=3)
            if len(d) >= 3 and d[0] == 0x59:
                for i in range(3, len(d) - 3, 4):
                    s = d[i + 3]
                    state = "actif" if s & 0x01 else "confirmé" if s & 0x08 else "en attente" if s & 0x04 else "mémorisé"
                    out.append({"source": f"UDS 19 02 · {name} ({ecu})", "code": f"{dtc_name(d[i], d[i + 1])}-{d[i + 2]:02X}",
                                "state": state, "status": f"{s:02X}"})
            elif len(d) >= 3 and d[0] == 0x7F:
                out.append({"source": f"UDS 19 02 · {name} ({ecu})", "code": None,
                            "state": f"lecture refusée : {NRC.get(d[2], f'code {d[2]:02X}')}"})
            else:
                out.append({"source": f"UDS 19 02 · {name} ({ecu})", "code": None, "state": "pas de réponse"})
        self._set(result={"dtc": out, "time": time.strftime("%Y-%m-%d %H:%M:%S")})

    def _scan(self, elm, job):
        ecu, dids = job["ecu"], job["dids"]
        if job["extended"]:
            self._set(phase="vérification : moteur coupé, voiture à l'arrêt")
            engine_stopped(elm)
        uds = Uds(elm, ecu)
        found, refused = {}, {}
        try:
            if job["extended"]:
                self._set(phase="ouverture de la session étendue (10 03)")
                uds.open_extended()
            self._set(phase="lecture des identifiants (22)")
            for i, did in enumerate(dids):
                if self.stop_flag.is_set():
                    break
                ok, data = uds.read(did)
                if ok:
                    found[did] = data
                elif ok is False:
                    refused[data] = refused.get(data, 0) + 1
                if i % 8 == 0 or ok:
                    self._set(done=i + 1, found=len(found))
        finally:
            back = uds.close_session()
        self._set(done=len(dids) if not self.stop_flag.is_set() else self.job["done"], found=len(found))
        self.dir.mkdir(parents=True, exist_ok=True)
        name = f"scan-{ecu}-{time.strftime('%Y%m%d-%H%M%S')}{'-etendue' if job['extended'] else ''}.json"
        body = {"ecu": ecu, "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "session": "étendue" if job["extended"] else "normale",
                "complete": not self.stop_flag.is_set(),
                "refused": {NRC.get(k, f"code {k:02X}"): v for k, v in refused.items()},
                "dids": {f"{d:04X}": v.hex().upper() for d, v in found.items()}}
        (self.dir / name).write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
        note = None if back else "retour en session normale non confirmé : il se fait seul au bout de ~5 s"
        self._set(file=name, error=note)

    # ---- scans enregistrés ----
    def _path(self, name):
        p = (self.dir / name).resolve()
        if p.parent != self.dir.resolve() or p.suffix != ".json" or not p.is_file():
            raise ValueError("scan introuvable")
        return p

    def list_scans(self):
        out = []
        for p in sorted(self.dir.glob("*.json"), reverse=True) if self.dir.is_dir() else []:
            try:
                b = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            ecu = b.get("ecu", "?")
            out.append({"name": p.name, "ecu": ecu, "ecu_name": ECUS.get(ecu, ("",))[0], "time": b.get("time"),
                        "session": b.get("session", "normale"), "n": len(b.get("dids", {})),
                        "complete": b.get("complete", True)})
        return out

    def describe(self, ecu, did, raw):
        """Nom et valeur d'un DID déjà connu du dashboard (moteur seulement), sinon (None, None)."""
        if ecu != "7E0" or did not in KNOWN_7E0 or len(raw) < 2:
            return None, None
        name, gain, offset = KNOWN_7E0[did]
        return name, round(int.from_bytes(raw[:2], "big") * gain + offset, 2)

    def load_scan(self, name):
        b = json.loads(self._path(name).read_text(encoding="utf-8"))
        rows = []
        for k, v in b.get("dids", {}).items():
            raw = bytes.fromhex(v)
            label, value = self.describe(b.get("ecu"), int(k, 16), raw)
            rows.append({"did": k, "raw": v, "label": label, "value": value})
        return {**{k: v for k, v in b.items() if k != "dids"}, "name": name, "rows": rows}

    def compare(self, names):
        """DID dont la valeur change d'un scan à l'autre (ou absents de certains) : ce qui bouge = mesure vivante."""
        if len(names) < 2:
            raise ValueError("choisir au moins deux scans")
        scans = [json.loads(self._path(n).read_text(encoding="utf-8")) for n in names]
        dids = sorted(set().union(*(s.get("dids", {}) for s in scans)), key=lambda d: int(d, 16))
        rows = []
        for d in dids:
            vals = [s.get("dids", {}).get(d) for s in scans]
            if len(set(vals)) > 1:
                label, _ = self.describe(scans[0].get("ecu"), int(d, 16), b"\0\0")
                rows.append({"did": d, "values": vals, "label": label})
        return {"scans": [{"name": n, "ecu": s.get("ecu"), "time": s.get("time"), "session": s.get("session", "normale")}
                          for n, s in zip(names, scans)],
                "total": len(dids), "rows": rows}
