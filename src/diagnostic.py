"""Onglet Diagnostic : codes défaut et scan des calculateurs, en lecture seule.

Véhicules au choix (VEHICLES) : la Fiesta VII et la Clio IV (CAN, UDS), et une Clio II phase 2 essence de 2002
(K-line, KWP2000 : pas de CAN sur cette prise OBD). Les requêtes, les calculateurs et la session de diagnostic
suivent le véhicule choisi sur la page ; par défaut, la voiture en cours du dashboard.

Le dashboard laisse l'ELM327 le temps du diagnostic (l'acquisition s'arrête, voir acquisition.py), le reprend
ensuite et l'enregistrement repart. Le diagnostic parle à l'adaptateur directement (commandes AT et requêtes
brutes), sans python-obd, pour pouvoir viser un calculateur précis (tableau de bord 743…).

Liste blanche (ALLOWED) vérifiée dans Elm.cmd, le seul endroit qui écrit sur le port : seules des lectures partent
vers la voiture, plus l'entrée / sortie de la session de diagnostic étendue (10 03 / 10 01) et son maintien (3E).
Tout le reste est refusé avant l'envoi : 10 02 (programmation), 27 (déverrouillage), 2E (écriture), 2F
(actionneurs), 31 (routines), 11 (redémarrage), 14 / 04 (effacement des défauts), 28, 85…
Les lignes CAN sont les mêmes que dans clio-iv-obd-utils (obd_scan.py) et doivent le rester ; les lignes KWP (K-line)
sont propres à ce dépôt.

Session étendue (10 03 en UDS, 10 C0 chez Renault en KWP) : moteur coupé et voiture à l'arrêt obligatoires (régime et vitesse lus juste avant), retour en
session normale à la fin, même sur erreur ou arrêt demandé.

Scans rangés dans data/diag/scan-*.json, au format de uds_scan.py (python uds_scan.py --compare data/diag/*.json),
codes défaut dans data/diag/dtc-*.json ; chacun avec le VIN lu au début du diagnostic (OBD 09 02). Ils partent
sur clio-sentinel-server, qui les range par VIN (synchro.sync_diag).
"""

import json
import re
import threading
import time
from pathlib import Path

import acquisition
import clio
import fiesta
import voitures

ALLOWED = [
    (r"AT.*", "réglage de l'adaptateur (ne part pas vers la voiture)"),
    (r"01[0-9A-F]{2}", "OBD 01 : valeur temps réel"),
    (r"09[0-9A-F]{2}", "OBD 09 : infos véhicule (VIN…)"),
    (r"0[37A]", "OBD 03 / 07 / 0A : lecture des codes défaut (mémorisés, en attente, permanents)"),
    (r"22[0-9A-F]{4}", "UDS 22 : lecture d'un identifiant"),
    (r"1902[0-9A-F]{2}", "UDS 19 02 : lecture des codes défaut"),
    (r"100[13]", "UDS 10 01 / 10 03 : session normale / étendue"),
    (r"3E[08]0", "UDS 3E : maintien de la session"),
    (r"21[0-9A-F]{2}", "KWP 21 : lecture d'un identifiant local (K-line)"),
    (r"1A[0-9A-F]{2}", "KWP 1A : identification du calculateur (K-line)"),
    (r"1802FF00", "KWP 18 02 : lecture des codes défaut (K-line)"),
    (r"10(?:81|C0)", "KWP 10 81 / 10 C0 : session normale / diagnostic Renault (K-line)"),
    (r"3E0[12]", "KWP 3E : maintien de la session (K-line)"),
]
_ALLOWED_RE = re.compile("|".join(f"(?:{p})" for p, _ in ALLOWED))

# Véhicules proposés sur la page : liaison, calculateurs (adresse : nom, plages fouillées par défaut)
# Clio II : injection à l'adresse de l'outil Renault (7A) et à son adresse OBD (10), jointes comme en OBD (init
# 5 bauds ou rapide, ISO 14230). ABS, airbag, UCH : non proposés (autre adresse possible).
# Fiesta : les calculateurs du réseau CAN Ford (adresses FORScan des Fiesta VII), réponse à l'adresse + 8 (726 →
# 72E). Ceux qui ne répondent pas (option absente sur cette voiture) sont signalés, pas en erreur. Moteur :
# identifiants Ford rangés par familles (OBDb : 03xx, 04xx, 05xx, 09xx, 11xx-13xx, 1Exx, F4xx) ; les autres :
# identification (F1xx) et données du calculateur (DDxx)
FORD_IDS = "DD00-DDFF,F100-F1FF"
VEHICLES = {
    "fiesta7": {"name": "Ford Fiesta VII 1.0 EcoBoost", "bus": "can", "car": fiesta, "reply": 8, "ecus": {
        "7E0": ("Moteur (PCM)", "0300-05FF,0900-09FF,1100-13FF,1E00-1EFF,F400-F4FF"),
        "760": ("ABS / ESP", FORD_IDS),
        "737": ("Airbags (RCM)", FORD_IDS),
        "730": ("Direction assistée (PSCM)", FORD_IDS),
        "726": ("Boîtier carrosserie (BCM)", FORD_IDS),
        "720": ("Combiné (IPC)", FORD_IDS),
        "716": ("Passerelle (GWM)", FORD_IDS),
        "7D0": ("SYNC (APIM)", FORD_IDS),
        "727": ("Audio (ACM)", FORD_IDS),
        "733": ("Climatisation (HVAC)", FORD_IDS),
        "736": ("Aide au stationnement (PAM)", FORD_IDS),
        "706": ("Caméra avant (IPMA)", FORD_IDS),
    }},
    "clio4": {"name": "Renault Clio IV dCi 90 (K9K)", "bus": "can", "car": clio, "ecus": {
        "7E0": ("Moteur", "2000-2FFF,F400-F4FF,FD00-FDFF"),
        "743": ("Tableau de bord", "0200-02FF,2000-21FF"),
    }},
    "clio2-ph2-2002-essence": {"name": "Renault Clio II phase 2 (2002) essence", "bus": "kline", "ecus": {
        "7A": ("Injection", "01-FF"),
        "10": ("Injection, adresse OBD", "01-FF"),
    }},
}
CAR_VEHICLE = {"fiesta": "fiesta7", "clio": "clio4"}   # voiture du dashboard → véhicule proposé par défaut
DEFAULT_VEHICLE = "clio4"   # scans d'avant la Fiesta : sans « vehicle », c'étaient ceux de la Clio IV
# Ce qui change selon la liaison : requête de lecture, taille d'un identifiant, sessions
BUS = {
    "can": {"read": "UDS 22", "id_digits": 4, "open": "10 03", "close": "10 01", "dtc": "UDS 19 02"},
    "kline": {"read": "KWP 21", "id_digits": 2, "open": "10 C0", "close": "10 81", "dtc": "KWP 18 02"},
}
MAX_DIDS = 16384
ADAPTER_WAIT_S = 20   # l'adaptateur redémarre après l'ATZ que python-obd envoie en rendant le port
KEEPALIVE_S = 2.0   # session étendue : « 3E 80 » si rien n'est parti depuis ce temps (le calculateur lâche à ~5 s)
FREE_TIMEOUT_S = 240  # attente que l'acquisition laisse l'adaptateur : sa recherche de protocole (python-obd, non
                      # interruptible) dure plusieurs minutes en K-line quand le calculateur ne répond pas
NRC = {0x11: "service non supporté", 0x12: "sous-fonction non supportée", 0x13: "longueur invalide",
       0x22: "conditions non remplies", 0x31: "identifiant inconnu", 0x33: "accès refusé (sécurité)",
       0x7E: "non supporté dans cette session", 0x7F: "service non supporté dans cette session",
       0x78: "réponse en attente"}
# Noms des DID du calculateur moteur déjà connus du dashboard (FALLBACK de clio.py, fiesta.py) : affichés à côté
# de la valeur brute. Plus le régime en F40C (OBD 01 0C relu en UDS), sur les deux.
LABELS = voitures.labels()
KNOWN_7E0 = {veh: {int(did, 16): (LABELS.get(key, key), gain, offset, nbytes, signed)
                   for key, (did, gain, offset, _rng, _p, nbytes, signed) in v["car"].FALLBACK.items()}
             | {0xF40C: ("Régime", 0.25, 0.0, 2, False)}
             for veh, v in VEHICLES.items() if "car" in v}


class Forbidden(Exception):
    """Requête hors de la liste blanche : jamais envoyée."""


class DiagError(Exception):
    pass


def check_allowed(command):
    if not _ALLOWED_RE.fullmatch(command.replace(" ", "").upper()):
        raise Forbidden(f"requête {command!r} refusée : hors de la liste blanche (lecture seule)")


def reply_addr(ecu, offset=None):
    """Adresse de réponse CAN 11 bits : 7E0 → 7E8 (OBD) ; sinon + offset du véhicule (Ford : + 8, 726 → 72E),
    par défaut + 0x20 comme chez Renault (743 → 763)."""
    n = int(ecu, 16)
    return f"{n + 8:03X}" if 0x7E0 <= n <= 0x7E7 else f"{n + (offset or 0x20):03X}"


# Octet d'état d'un code défaut UDS (ISO 14229, 19 02) : bit → sens
DTC_STATUS = {0x01: "défaut présent", 0x02: "défaillant pendant ce cycle", 0x04: "en attente",
              0x08: "confirmé", 0x10: "test non terminé depuis l'effacement", 0x20: "défaillant depuis l'effacement",
              0x40: "test non terminé pendant ce cycle", 0x80: "voyant demandé"}
DTC_FAULT = 0x01 | 0x04 | 0x08 | 0x20 | 0x80   # un vrai défaut, présent ou passé ; sinon : test pas encore fait
# Type de défaillance (3e octet des codes UDS, ISO 15031-6 / SAE J2012) : les plus courants
FTB = {0x00: None, 0x01: "défaut électrique général", 0x02: "défaut de signal général", 0x03: "signal FM / MLI",
       0x04: "défaut interne du système", 0x05: "programmation du système", 0x06: "algorithme", 0x07: "défaut mécanique",
       0x08: "défaut de signal bus", 0x09: "défaut de composant", 0x11: "court-circuit à la masse",
       0x12: "court-circuit au + batterie", 0x13: "circuit ouvert", 0x14: "court-circuit à la masse ou circuit ouvert",
       0x15: "court-circuit au + ou circuit ouvert", 0x16: "tension trop basse", 0x17: "tension trop haute",
       0x18: "courant trop bas", 0x19: "courant trop haut", 0x1A: "résistance trop basse",
       0x1B: "résistance trop haute", 0x1C: "tension hors plage", 0x1D: "courant hors plage",
       0x1E: "résistance hors plage", 0x1F: "circuit intermittent", 0x21: "amplitude du signal trop basse",
       0x22: "amplitude du signal trop haute", 0x23: "signal bloqué bas", 0x24: "signal bloqué haut",
       0x25: "forme de signal incorrecte", 0x26: "taux de variation trop bas", 0x27: "taux de variation trop haut",
       0x28: "décalage ou zéro du signal", 0x29: "signal invalide", 0x2A: "signal figé", 0x2F: "signal erratique",
       0x31: "pas de signal", 0x41: "erreur de somme de contrôle", 0x42: "erreur mémoire", 0x44: "erreur mémoire de données",
       0x45: "erreur mémoire programme", 0x46: "erreur de calibration / paramétrage", 0x47: "chien de garde / sécurité",
       0x48: "erreur de supervision", 0x49: "défaut électronique interne", 0x4B: "température trop haute",
       0x51: "non programmé", 0x54: "calibration manquante", 0x55: "non configuré", 0x56: "configuration invalide",
       0x62: "comparaison de signaux en défaut", 0x63: "temporisation de circuit / composant",
       0x64: "plausibilité du signal", 0x67: "signal incorrect après action", 0x68: "compteur d'événements",
       0x71: "actionneur bloqué", 0x72: "actionneur bloqué ouvert", 0x73: "actionneur bloqué fermé",
       0x77: "position commandée non atteinte", 0x7B: "niveau de fluide bas", 0x81: "message série invalide",
       0x82: "compteur de vie / message", 0x83: "protection de message", 0x84: "signal sous la plage",
       0x85: "signal au-dessus de la plage", 0x86: "signal invalide reçu", 0x87: "message manquant",
       0x88: "bus off", 0x8F: "erratique", 0x92: "performance ou réponse incorrecte", 0x96: "défaut interne du composant",
       0x98: "température du composant trop haute"}


def dtc_desc(code):
    """Description d'un code générique SAE (P0…, P2…, U0…, B0…, C0… : table de python-obd, en anglais), sinon None
    (codes propres au constructeur, P1… ou B1…)."""
    try:
        from obd.codes import DTC
    except ImportError:
        return None
    return DTC.get(code)


def status_flags(s):
    """Octet d'état → liste des bits levés, en clair."""
    return [txt for bit, txt in DTC_STATUS.items() if s & bit]


def dtc_state(s):
    """État résumé d'un code UDS, du plus grave au plus ancien."""
    if s & 0x01:
        return "présent"
    if s & 0x08:
        return "confirmé"
    if s & 0x04:
        return "en attente"
    return "historique"   # défaillant depuis l'effacement, plus maintenant


def vehicle(key):
    if key not in VEHICLES:
        raise ValueError("véhicule inconnu")
    return VEHICLES[key]


def parse_ranges(text, digits=4):
    dids = []
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        lo, _, hi = part.partition("-")
        lo, hi = int(lo, 16), int(hi or lo, 16)
        if not (0 <= lo <= hi < 16 ** digits):
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


def kline_message(b):
    """Une ligne K-line (en-têtes affichés) → (adresse source, données). KWP (ISO 14230) : octet de format
    80 + longueur, cible, source[, longueur] ; ISO 9141 (48 / 68…) : 3 octets d'en-tête et la somme de contrôle."""
    if len(b) < 4:
        return None, b""
    if b[0] & 0x80:
        n, start = b[0] & 0x3F, 3
        if n == 0:
            n, start = b[3], 4
        return b[2], b[start:start + n]
    return b[2], b[3:-1]


def obd_dtcs(d, can):
    """Réponse OBD 03 / 07 → codes. En CAN, un octet de nombre suit le service ; en K-line, 3 codes par message."""
    start = 2 if can else 1
    end = min(len(d), start + 2 * d[1]) if can and len(d) > 1 else len(d)
    return [dtc_name(d[i], d[i + 1]) for i in range(start, end - 1, 2) if d[i] or d[i + 1]]


def dtc_name(b0, b1):
    """Deux octets → code SAE, ex. 04 01 → P0401."""
    return "PCBU"[b0 >> 6] + str((b0 >> 4) & 3) + f"{b0 & 0xF:X}{b1:02X}"


class Elm:
    """ELM327 en commandes brutes. Toute requête passe par cmd(), qui applique la liste blanche."""

    def __init__(self, port, baud, bus="can", timeout=2.0, journal=None):
        import serial
        self.timeout, self.bus, self.journal, self.proto = timeout, bus, journal, None
        # write_timeout : un adaptateur qui ne lit plus (redémarrage après ATZ…) ne bloque pas le diagnostic
        self.ser = serial.serial_for_url(port, baudrate=baud or 38400, timeout=0.2, write_timeout=2)

    def cmd(self, command, timeout=None):
        check_allowed(command)
        self.ser.reset_input_buffer()
        self.ser.write((command + "\r").encode("ascii"))
        buf, end = b"", time.time() + (timeout or self.timeout)
        while time.time() < end and b">" not in buf:
            buf += self.ser.read(self.ser.in_waiting or 1)
        text = buf.decode("ascii", "ignore").replace(">", "")
        lines = [x.strip() for x in text.replace("\r", "\n").split("\n") if x.strip() and x.strip() != command]
        self.note(f"> {command}", *(f"< {x}" for x in lines))
        return lines

    def note(self, *lines):
        """Journal des échanges (data/diag/*.log) : de quoi comprendre un premier contact avec un calculateur."""
        if self.journal:
            with open(self.journal, "a", encoding="utf-8") as f:
                f.writelines(f"{time.strftime('%H:%M:%S')} {x}\n" for x in lines)

    def frames(self, command, timeout=None):
        """Réponse par calculateur : {en-tête: données réassemblées} (CAN 11 bits, en-têtes affichés).
        En K-line : {adresse source: [données de chaque message]}, un calculateur pouvant répondre en plusieurs."""
        by = {}
        for line in self.cmd(command, timeout):
            line = line.replace(" ", "").upper()
            if len(line) < 5 or not re.fullmatch(r"[0-9A-F]+", line):
                continue   # NO DATA, SEARCHING…, BUS INIT…, ?
            try:
                if self.bus == "can":
                    by.setdefault(line[:3], []).append(bytes.fromhex(line[3:]))
                elif len(line) % 2 == 0:
                    src, data = kline_message(bytes.fromhex(line))
                    if data:
                        by.setdefault(f"{src:02X}", []).append(data)
            except ValueError:
                pass
        return {h: reassemble(c) for h, c in by.items()} if self.bus == "can" else by

    def initialize(self, bus="can"):
        """Trouve le protocole par une requête OBD (0100). En K-line, un calculateur seul (sur table) peut ne pas
        répondre en OBD : on continue quand même vers ses requêtes Renault. Rend True si l'OBD répond."""
        # ATD (réglages par défaut) plutôt qu'ATZ : certaines clés USB redémarrent sur ATZ et se déconnectent
        # ~2 s (ttyACM recréé), en plein diagnostic. python-obd vient de toute façon d'en envoyer un en rendant le port.
        for c, t in (("ATD", 3), ("ATE0", None), ("ATL0", None), ("ATS0", None), ("ATH1", None),
                     ("ATAT1", None), ("ATSP0", None)):
            self.cmd(c, t)
        resp = self.cmd("0100", timeout=20)   # lance la recherche du protocole
        if not any("4100" in x.replace(" ", "") for x in resp):
            if bus == "kline":
                self.note("pas de réponse OBD : suite en KWP Renault directement")
                return False
            raise DiagError("pas de réponse de la voiture (contact mis ?)")
        num = self.cmd("ATDPN")
        proto = self.proto = num[0].lstrip("A") if num else "?"
        if bus == "can" and proto not in ("6", "8"):
            raise DiagError(f"protocole {proto} : ce véhicule attend du CAN 11 bits (mauvais véhicule choisi ?)")
        if bus == "kline" and proto not in ("3", "4", "5"):
            raise DiagError(f"protocole {proto} : ce véhicule attend une K-line (mauvais véhicule choisi ?)")
        return True

    def close(self):
        try:
            self.ser.close()
        except OSError:
            pass


class Uds:
    """Requêtes UDS adressées à un calculateur."""

    def __init__(self, elm, ecu, offset=None):
        self.elm, self.ecu, self.rx = elm, ecu, reply_addr(ecu, offset)
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


class Kwp:
    """Requêtes KWP2000 sur K-line adressées à un calculateur Renault (Clio II), même interface que Uds.
    Initialisation vers l'adresse du calculateur à la première requête, comme celle trouvée en OBD : 5 bauds
    (ATSP4, adresse d'init ATIIA) si le calculateur s'est réveillé ainsi, sinon rapide (ATSP5). L'ELM327
    entretient ensuite la liaison tout seul en envoyant « 3E » (présence du testeur) toutes les ~3 s (ATWM)."""

    def __init__(self, elm, ecu):
        self.elm, self.ecu, self.rx = elm, ecu, ecu
        self.extended, self.first = False, True
        slow = elm.proto in ("3", "4")
        for c in ("ATPC", f"ATSH81{ecu}F1", f"ATWM81{ecu}F13E", *((f"ATIIA{ecu}",) if slow else ()),
                  "ATSP4" if slow else "ATSP5"):
            elm.cmd(c)

    def request(self, payload, timeout=2):
        if self.first:   # l'init passe avant la première réponse (≈ 2 s à 5 bauds, plus si l'ELM réessaie)
            self.first, timeout = False, max(timeout, 10)
        msgs = self.elm.frames(payload, timeout).get(self.rx, [])
        msgs = [m for m in msgs if not (len(m) >= 3 and m[0] == 0x7F and m[2] == 0x78)]   # « réponse en attente »
        return msgs[-1] if msgs else b""

    def read(self, lid):
        d = self.request(f"21{lid:02X}")
        if len(d) >= 2 and d[0] == 0x61 and d[1] == lid:
            return True, d[2:]
        if len(d) >= 3 and d[0] == 0x7F and d[1] == 0x21:
            return False, d[2]
        return None, None

    def open_extended(self):
        d = self.request("10C0")
        if len(d) >= 2 and d[0] == 0x50 and d[1] == 0xC0:
            self.extended = True
            return
        if len(d) >= 3 and d[0] == 0x7F:
            raise DiagError(f"session de diagnostic refusée par le calculateur : {NRC.get(d[2], f'code {d[2]:02X}')}")
        raise DiagError("session de diagnostic : pas de réponse du calculateur")

    def close_session(self):
        if not self.extended:
            return True
        self.extended = False
        for _ in range(2):
            d = self.request("1081")
            if len(d) >= 2 and d[0] == 0x50 and d[1] == 0x81:
                return True
        return False


def read_vin(elm):
    """VIN (OBD 09 02, à tous les calculateurs), ou None. En CAN, une réponse en plusieurs trames réassemblée :
    49 02 01 + 17 caractères ; en K-line, 5 messages 49 02 NN + 4 octets."""
    if elm.bus == "can":
        elm.cmd("ATSH7DF")
        elm.cmd("ATCRA")
    parts = {}
    for _src, d in obd_sources(elm, "0902", timeout=3):
        if len(d) >= 4 and d[0] == 0x49 and d[1] == 0x02:
            if elm.bus == "can":
                parts.setdefault(0, d[3:])
            else:
                parts[d[2]] = d[3:]
    raw = b"".join(parts[k] for k in sorted(parts))
    vin = "".join(chr(c) for c in raw if chr(c).isalnum())[-17:]
    return vin if len(vin) == 17 else None


def engine_stopped(elm):
    """Lève DiagError si le moteur tourne ou si la voiture roule (requête à tous les calculateurs : 7DF en CAN,
    en-tête OBD par défaut en K-line, donc à appeler avant de viser un calculateur)."""
    if elm.bus == "can":
        elm.cmd("ATSH7DF")
        elm.cmd("ATCRA")   # écoute de toutes les réponses
    rpm = [int.from_bytes(d[2:4], "big") / 4 for d in obd_replies(elm, "010C") if len(d) >= 4 and d[0] == 0x41]
    speed = [d[2] for d in obd_replies(elm, "010D") if len(d) >= 3 and d[0] == 0x41]
    if not rpm:
        raise DiagError("régime illisible en OBD : impossible de vérifier que le moteur est coupé, session étendue "
                        "refusée (scan possible sans elle)")
    if max(rpm) > 0:
        raise DiagError(f"moteur tournant ({max(rpm):.0f} tr/min) : couper le moteur, laisser le contact mis")
    if speed and max(speed) > 0:
        raise DiagError(f"voiture en mouvement ({max(speed)} km/h)")


def obd_replies(elm, command, timeout=None):
    """Réponses OBD de tous les calculateurs, à plat : [(source, données)] → [données]."""
    return [d for _s, d in obd_sources(elm, command, timeout)]


def obd_sources(elm, command, timeout=None):
    out = []
    for src, v in elm.frames(command, timeout).items():
        out += [(src, d) for d in (v if isinstance(v, list) else [v])]
    return out


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
        return {"job": job, "scans": self.list_scans(), "default": CAR_VEHICLE.get(self.state.car.ID, DEFAULT_VEHICLE),
                "vehicles": {k: {"name": v["name"], **BUS[v["bus"]], "bus": v["bus"],
                                 "ecus": {a: {"name": n, "ranges": r} for a, (n, r) in v["ecus"].items()},
                                 "order": list(v["ecus"])}   # en JS, « 760 » passerait avant « 7E0 »
                             for k, v in VEHICLES.items()},
                "allowed": [d for _p, d in ALLOWED]}

    def start(self, req):
        """Lance un diagnostic : {"job": "dtc", "vehicle"} ou {"job": "scan", "vehicle", "ecu", "ranges", "extended"}.
        ValueError si refusé."""
        if self.state.get_settings().get("demo"):
            raise ValueError("arrêter le mode démo d'abord (onglet Réglages)")
        kind, veh = req.get("job"), str(req.get("vehicle") or CAR_VEHICLE.get(self.state.car.ID, DEFAULT_VEHICLE))
        v = vehicle(veh)
        if kind == "scan":
            ecu = str(req.get("ecu", "")).upper()
            if v["bus"] == "can" and (not re.fullmatch(r"[0-7][0-9A-F]{2}", ecu) or ecu == "7DF"):
                raise ValueError("adresse de calculateur invalide (3 chiffres hexa, ex. 743)")
            if v["bus"] == "kline" and (not re.fullmatch(r"[0-9A-F]{2}", ecu) or ecu in ("33", "F1")):
                raise ValueError("adresse de calculateur invalide (2 chiffres hexa, ex. 7A)")
            digits = BUS[v["bus"]]["id_digits"]
            default = v["ecus"].get(ecu, ("", "01-FF" if digits == 2 else "2000-2FFF"))[1]
            dids = parse_ranges(str(req.get("ranges") or default), digits)
            job = {"kind": "scan", "ecu": ecu, "extended": bool(req.get("extended")), "dids": dids}
        elif kind == "dtc":
            job = {"kind": "dtc"}
        else:
            raise ValueError("diagnostic inconnu")
        job["vehicle"] = veh
        with self.lock:
            if self.job and self.job.get("running"):
                raise ValueError("un diagnostic est déjà en cours")
            self.stop_flag.clear()
            self.job = {"kind": kind, "running": True,
                        "phase": "attente de l'adaptateur (l'enregistrement finit sa tentative de connexion)", "started": time.time(),
                        "vehicle": veh, "ecu": job.get("ecu"), "extended": job.get("extended", False),
                        "done": 0, "total": len(job.get("dids", [])), "found": 0, "error": None,
                        "result": None, "file": None, "journal": None}
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
                raise DiagError(f"l'enregistrement n'a pas laissé l'adaptateur en {FREE_TIMEOUT_S // 60} min (sa connexion "
                                "au calculateur est bloquée : calculateur alimenté ? adaptateur à rebrancher ?)")
            port, baud = self.args.port, self.args.baud
            if not port and st.port and (str(st.port).startswith("/dev/") and Path(st.port).exists()
                                         or re.fullmatch(r"COM\d+", str(st.port))):
                # Port que l'enregistrement vient de rendre, adaptateur laissé tel quel (sans ATZ, acquisition.py)
                fd = acquisition.lock_port(st.port)
                if fd is not None:
                    port, baud = st.port, baud or getattr(st, "baud", None)
            if not port:
                # python-obd remet l'adaptateur à zéro (ATZ) en rendant le port : il ne répond qu'après
                # son redémarrage (quelques secondes, plus si la clé USB se reconnecte)
                self._set(phase="recherche de l'adaptateur")
                end = time.time() + ADAPTER_WAIT_S
                while True:
                    try:
                        port, found, name, fd, _ = acquisition.find_adapter(self.args.dev)
                    except OSError:   # port disparu pendant la recherche (clé qui se reconnecte)
                        port = fd = None
                    if port or time.time() > end or self.stop_flag.is_set():
                        break
                    time.sleep(1)
                if not port:
                    raise DiagError(f"aucun ELM327 ne répond (cherché pendant {ADAPTER_WAIT_S} s)")
                baud = baud or found
            else:
                fd = acquisition.lock_port(port)
                if fd is None:
                    raise DiagError(f"{port} est utilisé par un autre programme")
            self._set(phase="connexion à la voiture")
            bus = VEHICLES[job["vehicle"]]["bus"]
            self.dir.mkdir(parents=True, exist_ok=True)
            journal = f"{job['kind']}-{job.get('ecu') or job['vehicle']}-{time.strftime('%Y%m%d-%H%M%S')}.log"
            self._set(journal=journal)
            elm = Elm(port, baud, bus, journal=self.dir / journal)
            job["obd"] = elm.initialize(bus)
            # VIN de la voiture diagnostiquée : lu ici, sinon celui de la dernière connexion si c'est la même voiture
            vin = read_vin(elm) if job["obd"] else None
            car = VEHICLES[job["vehicle"]].get("car")
            if not vin and car is self.state.car and self.state.vin and voitures.detect(self.state.vin) == car.ID:
                vin = self.state.vin
            job["vin"] = vin
            self._set(vin=vin)
            if job["kind"] == "dtc":
                self._dtc(elm, VEHICLES[job["vehicle"]], job["obd"], job)
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

    def _dtc(self, elm, veh, obd=True, job=None):
        """Codes défaut : OBD 03 (mémorisés), 07 (en attente) et 0A (permanents) à tous les calculateurs, puis ceux
        du constructeur (UDS 19 02 en CAN, KWP 18 02 en K-line) sur chaque calculateur du véhicule. Rien n'est effacé.

        En UDS, seuls les codes en défaut sont demandés (masque d'état AD : présent, en attente, confirmé, défaillant
        depuis l'effacement, voyant) : avec FF, le calculateur renvoie aussi ceux dont le test n'a pas encore tourné,
        des centaines sur un PCM Ford, en une réponse que certains ELM327 coupent. Un calculateur qui ignore le masque
        les renvoie quand même : ils sont alors écartés et seulement comptés. Chaque code garde son état bit par bit, son type
        de défaillance (3e octet) et sa description quand c'est un code générique."""
        out, can = [], veh["bus"] == "can"
        self._set(phase="codes défaut OBD (03 / 07 / 0A)")
        if can:
            elm.cmd("ATSH7DF")
            elm.cmd("ATCRA")
        if not obd:
            out.append({"source": "OBD 03 / 07 / 0A", "code": None, "state": "pas de réponse OBD (calculateur seul ?)"})
        for mode, kind in (("03", "mémorisé"), ("07", "en attente"), ("0A", "permanent")) if obd else ():
            for src, d in obd_sources(elm, mode, timeout=3):
                if d and d[0] == 0x40 + int(mode, 16):
                    out += [{"source": f"OBD {mode} · {src}", "code": c, "state": kind, "desc": dtc_desc(c)}
                            for c in obd_dtcs(d, can)]
        offset = veh.get("reply")
        for ecu, (name, _r) in veh["ecus"].items():
            if self.stop_flag.is_set():
                break
            src = f"{BUS[veh['bus']]['dtc']} · {name} ({ecu})"
            self._set(phase=f"codes défaut · {name} ({ecu})")
            if can:
                d = Uds(elm, ecu, offset).request(f"1902{DTC_FAULT:02X}", timeout=3)
                ok, step = len(d) >= 3 and d[0] == 0x59, 4
            else:
                d = Kwp(elm, ecu).request("1802FF00", timeout=3)
                ok, step = len(d) >= 2 and d[0] == 0x58, 3
            if ok:
                found, untested = 0, 0
                for i in range(3 if can else 2, len(d) - step + 1, step):
                    s = d[i + step - 1]
                    if can:
                        if not s & DTC_FAULT:   # test pas encore fait : pas un défaut
                            untested += 1
                            continue
                        base = dtc_name(d[i], d[i + 1])
                        ftb = d[i + 2]
                        row = {"source": src, "ecu": ecu, "code": f"{base}-{ftb:02X}", "state": dtc_state(s),
                               "status": f"{s:02X}", "flags": status_flags(s), "desc": dtc_desc(base),
                               "ftb": FTB.get(ftb, f"type {ftb:02X}"), "mil": bool(s & 0x80)}
                    else:   # codes Renault (DF… sur l'outil du garage) : laissés en hexa, état ISO 14230
                        row = {"source": src, "ecu": ecu, "code": f"{d[i]:02X}{d[i + 1]:02X} ({dtc_name(d[i], d[i + 1])})",
                               "state": "présent" if (s >> 5) & 3 in (1, 3) else "mémorisé", "status": f"{s:02X}"}
                    out.append(row)
                    found += 1
                if not found:
                    out.append({"source": src, "ecu": ecu, "code": None, "state": "aucun défaut"
                                + (f" ({untested} test{'s' if untested > 1 else ''} pas encore fait"
                                   f"{'s' if untested > 1 else ''})" if untested else "")})
                elif untested:
                    out.append({"source": src, "ecu": ecu, "code": None,
                                "state": f"{untested} autre{'s' if untested > 1 else ''} code{'s' if untested > 1 else ''}"
                                         f" au test pas encore fait (pas des défauts)"})
            elif len(d) >= 3 and d[0] == 0x7F:
                out.append({"source": src, "ecu": ecu, "code": None,
                            "state": f"lecture refusée : {NRC.get(d[2], f'code {d[2]:02X}')}"})
            else:
                out.append({"source": src, "ecu": ecu, "code": None, "state": "pas de réponse (absent sur cette voiture ?)"})
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self._set(result={"dtc": out, "time": now})
        # Gardés (le serveur les reçoit, rangés par VIN), même sans code : « aucun code ce jour-là » compte aussi
        if job is not None and not self.stop_flag.is_set():
            self.dir.mkdir(parents=True, exist_ok=True)
            name = f"dtc-{job['vehicle']}-{time.strftime('%Y%m%d-%H%M%S')}.json"
            body = {"kind": "dtc", "vehicle": job["vehicle"], "vin": job.get("vin"), "time": now, "dtc": out}
            (self.dir / name).write_text(json.dumps(body, indent=1, ensure_ascii=False), encoding="utf-8")
            self._set(file=name)

    def _scan(self, elm, job):
        ecu, dids, bus = job["ecu"], job["dids"], BUS[VEHICLES[job["vehicle"]]["bus"]]
        if job["extended"]:
            self._set(phase="vérification : moteur coupé, voiture à l'arrêt")
            engine_stopped(elm)
        offset = VEHICLES[job["vehicle"]].get("reply")
        uds = Uds(elm, ecu, offset) if elm.bus == "can" else Kwp(elm, ecu)
        found, refused = {}, {}
        try:
            if job["extended"]:
                self._set(phase=f"ouverture de la session étendue ({bus['open']})")
                uds.open_extended()
            self._set(phase=f"lecture des identifiants ({bus['read']})")
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
        body = {"kind": "scan", "vehicle": job["vehicle"], "vin": job.get("vin"), "ecu": ecu,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "session": "étendue" if job["extended"] else "normale",
                "complete": not self.stop_flag.is_set(),
                "refused": {NRC.get(k, f"code {k:02X}"): v for k, v in refused.items()},
                "dids": {f"{d:0{bus['id_digits']}X}": v.hex().upper() for d, v in found.items()}}
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
            if b.get("kind") == "dtc":   # codes défaut : pas des scans (pas d'identifiants à comparer)
                continue
            ecu, veh = b.get("ecu", "?"), VEHICLES.get(b.get("vehicle", DEFAULT_VEHICLE), {})
            out.append({"name": p.name, "ecu": ecu, "ecu_name": veh.get("ecus", {}).get(ecu, ("",))[0],
                        "vehicle": veh.get("name", b.get("vehicle")), "time": b.get("time"),
                        "session": b.get("session", "normale"), "n": len(b.get("dids", {})),
                        "complete": b.get("complete", True)})
        return out

    def describe(self, scan, did, raw):
        """Nom et valeur d'un DID déjà connu du dashboard (calculateur moteur de la Fiesta ou de la Clio IV),
        sinon (None, None)."""
        known = KNOWN_7E0.get(scan.get("vehicle", DEFAULT_VEHICLE), {})
        if scan.get("ecu") != "7E0" or did not in known:
            return None, None
        name, gain, offset, nbytes, signed = known[did]
        if len(raw) < nbytes:
            return None, None
        return name, round(int.from_bytes(raw[:nbytes], "big", signed=signed) * gain + offset, 2)

    def load_scan(self, name):
        b = json.loads(self._path(name).read_text(encoding="utf-8"))
        rows = []
        for k, v in b.get("dids", {}).items():
            raw = bytes.fromhex(v)
            label, value = self.describe(b, int(k, 16), raw)
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
                label, _ = self.describe(scans[0], int(d, 16), b"\0\0")
                rows.append({"did": d, "values": vals, "label": label})
        return {"scans": [{"name": n, "ecu": s.get("ecu"), "time": s.get("time"), "session": s.get("session", "normale")}
                          for n, s in zip(names, scans)],
                "total": len(dids), "rows": rows}
