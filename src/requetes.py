"""Lecture rapide du calculateur : moins de requêtes par tour, et des requêtes plus courtes.

Sans ça, chaque mesure est une requête à part (≈ 45 ms aller-retour sur la Fiesta), toutes à chaque tour : avec
une trentaine de mesures, un tour dure 1,3 s (0,8 Hz). Trois leviers, chacun essayé à la connexion et abandonné
si le calculateur ou l'adaptateur ne suit pas :

  priorités   chaque mesure a sa période (PERIOD) : régime, vitesse, pédale… à chaque tour, les températures
              toutes les 2 s, le niveau de carburant toutes les 10 s
  groupage    jusqu'à 6 PID OBD dans une requête (« 01 0C 0D 49 11 0B 04 », permis par l'OBD sur CAN), et
              plusieurs identifiants constructeur dans une requête UDS (« 22 033E 0462 »), si le calculateur
              l'accepte
  adressage   requêtes envoyées au seul calculateur moteur (ATSH 7E0) au lieu de tous (7DF), et nombre de
              réponses attendu ajouté à la requête (« 010C0D1 ») : l'ELM327 rend la main dès la réponse reçue,
              au lieu d'attendre que d'autres calculateurs répondent

Le calculateur peut ne rendre qu'une partie d'un groupe (réponse limitée à une trame) : le groupe est alors relu
mesure par mesure, et la taille des groupes ramenée à ce qu'il a rendu. Le scan des PID (scan_pid.py) mesure tout
ça à l'avance, essai après essai, et laisse dans data/scan-pid-<voiture>.json la façon la plus rapide qui répond
à tous les coups (taille des groupes, délai d'attente de l'ELM327) : reprise ici à la connexion.

Les requêtes passent directement par l'interface ELM327 de python-obd (send_and_parse) ; les réponses sont
découpées mesure par mesure et décodées par les décodeurs habituels (python-obd, constructeur.py).
"""

import json
import time
from pathlib import Path

from obd.protocols.protocol import Message

# Période de lecture (s) de chaque mesure, 0 : à chaque tour. Les mesures constructeur gardent celle de leur
# FALLBACK (clio.py, fiesta.py). Rapides : ce que suivent les trajets (vitesse, régime, pédale, conso) et ce qui
# bouge sous le pied.
PERIOD = {
    "rpm": 0, "speed": 0, "pedal": 0, "throttle": 0, "map": 0, "load": 0, "fuel": 0, "timing": 0,
    "maf": 0.5, "lambda": 0.5, "stft": 0.5, "rail": 0.5, "egr": 0.5, "egr_err": 0.5,
    "coolant": 2, "oil": 2, "intake": 2, "cat": 2, "voltage": 2, "ltft": 2,
    "baro": 5, "ambient": 5, "ethanol": 5, "fuel_lvl": 10,
}
MAX_OBD = 6      # PID par requête OBD (limite de la norme)
MAX_UDS = 4      # identifiants constructeur par requête UDS (prudent : la taille de réponse dépend du calculateur)
MAX_EXTRA = 1    # requêtes par tour pour les mesures qui ne sont pas lues à chaque tour (tick)
PING_EMA = 0.1   # lissage du temps de réponse affiché
SCAN_DIR = Path(__file__).resolve().parent.parent / "data"   # scan-pid-<voiture>.json, écrit par scan_pid.py


def scan_file(car):
    return SCAN_DIR / f"scan-pid-{car.ID}.json"


def load_scan(vin, car, path=None):
    """Réglages trouvés par le dernier scan des PID, s'il a été fait sur cette voiture ; sinon None."""
    path = path or scan_file(car)
    try:
        scan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(scan, dict) or (scan.get("vin") and vin and scan["vin"] != vin):
        return None
    return scan


class Reader:
    """Mesures à lire et façon de les demander, choisie à la connexion (setup), puis lues tour après tour (tick)."""

    def __init__(self, conn, cmds, car, scan=None, slow=False):
        """cmds : (clé, commande, conversion, lecture espacée ou None, requête forcée), comme acquisition.py ;
        car : la voiture (identifiants de ses mesures constructeur) ; scan : résultat de scan_pid.py (load_scan) ;
        slow : adaptateur lent (clone, acquisition.import_obd) : taille des groupes essayée en entier à la
        connexion, et requêtes des mesures qui ne sont pas lues à chaque tour limitées (MAX_EXTRA)."""
        self.conn, self.elm, self.car, self.scan, self.slow = conn, conn.interface, car, scan or {}, slow
        self.items = []   # [clé, commande, conversion, constructeur ?, période, dernière lecture]
        for key, cmd, conv, slow, _force in cmds:
            period = slow.period if slow is not None else PERIOD.get(key, 1)
            self.items.append([key, cmd, conv, slow is not None, period, 0.0])
        self.frames = {}        # requête → nombre de trames de la réponse (suffixe « nombre de réponses »)
        self.no_count = set()   # requêtes que le suffixe tronque : envoyées sans
        self.count_ok = False   # l'adaptateur accepte le suffixe
        self.max_obd = self.max_uds = 1   # mesures par requête (1 : pas de groupage)
        self.ping_ms = None     # temps de réponse moyen d'une requête

    # --- Connexion : ce que l'adaptateur et le calculateur acceptent ---

    def setup(self):
        """Essaie adressage direct, suffixe et groupage ; renvoie un résumé pour le journal."""
        notes = []
        # Délai d'attente de l'ELM327 trouvé par le scan des PID (ATST, ATAT) : gardé si l'adaptateur l'accepte
        at = [c for c in self.scan.get("at", []) if self._send_at(c.encode())]
        if at:
            notes.append("délai d'attente du scan (" + " ".join(at) + ")")
        obd_items = [it for it in self.items if not it[3]]
        uds_items = [it for it in self.items if it[3]]
        # Essai sur des mesures qui répondent à coup sûr moteur tournant ou non (régime, vitesse)
        probe = [it for it in obd_items if it[0] in ("rpm", "speed")] or obd_items[:2]
        if probe and self._answers(probe[:1], False):
            if self._send_at(b"ATSH7E0"):
                # Calculateur moteur en adresse physique : il doit répondre seul, sinon retour à la diffusion (7DF)
                if self._answers(probe[:1], False):
                    notes.append("requêtes au seul calculateur moteur (7E0)")
                else:
                    self._send_at(b"ATSH7DF")
            self.count_ok = self._answers(probe[:1], True)
            if self.count_ok:
                notes.append("réponse sans attente (nombre de réponses)")
        max_obd = min(MAX_OBD, self.scan.get("max_obd", MAX_OBD))
        max_uds = min(MAX_UDS, self.scan.get("max_uds", MAX_UDS))
        if not self.slow:
            # Adaptateur rapide : groupage essayé sur deux mesures, ramené en roulant si le calculateur rend moins
            if len(probe) == 2 and max_obd > 1 and self._answers(probe):
                self.max_obd = max_obd
                notes.append(f"jusqu'à {max_obd} PID OBD par requête")
            if len(uds_items) >= 2 and max_uds > 1 and self._answers(uds_items[:2]):
                self.max_uds = max_uds
                notes.append(f"jusqu'à {max_uds} mesures {self.car.MAKER} par requête")
            return notes
        # Clone lent : groupes essayés à leur vraie taille, du plus grand au plus petit (des clones rendent 2 PID
        # par requête et « NO DATA » au-delà, sans réponse partielle qui réduise les groupes en roulant : chaque
        # tour relirait alors chaque groupe refusé mesure par mesure)
        if probe and self._answers(probe[:1], False):
            obd_items = probe + [it for it in obd_items if it not in probe]
            self.max_obd = self._largest(obd_items, max_obd)
            if self.max_obd > 1:
                notes.append(f"jusqu'à {self.max_obd} PID OBD par requête")
        self.max_uds = self._largest(uds_items, max_uds)
        if self.max_uds > 1:
            notes.append(f"jusqu'à {self.max_uds} mesures {self.car.MAKER} par requête")
        return notes

    def _largest(self, items, most):
        """Plus grand groupe (≤ most) des premières mesures auquel le calculateur répond en entier, 1 sinon."""
        for n in range(min(most, len(items)), 1, -1):
            if self._answers(items[:n]):
                return n
        return 1

    def _answers(self, group, count=None):
        """Toutes les mesures du groupe répondent-elles à cette façon de demander ?"""
        values = self._read(group, count)
        return values is not None and all(v is not None for v in values.values())

    def _send_at(self, cmd):
        try:
            self.elm.send_and_parse(cmd)
            return True
        except Exception:
            return False

    # --- Tour de lecture ---

    def tick(self, state, delay=0.0):
        """Lit les mesures dues ; renvoie True si le calculateur a répondu au moins une fois."""
        now = time.time()
        # Adaptateur lent : mesures de chaque tour d'abord, puis les plus en retard ; requêtes de ces dernières
        # limitées à MAX_EXTRA par tour : à ≈ 70 ms par commande (clones), un tour qui les lirait toutes durerait
        # plus que leur période, toutes seraient dues à chaque tour et le régime, la vitesse ou la pédale ne
        # seraient relus que toutes les ≈ 0,7 s. Celles laissées passent au tour suivant.
        due = [it for it in self.items if now - it[5] >= it[4]]
        if self.slow:
            due.sort(key=lambda it: (it[4] > 0, it[5]))
        groups = self._groups(due)
        if self.slow:
            every = [g for g in groups if any(it[4] == 0 for it in g)]
            groups = every + [g for g in groups if g not in every][:MAX_EXTRA]
        for group in groups:
            for it in group:
                it[5] = now
        answered = False
        for group in groups:
            values = self._read(group)
            if values is None and len(group) > 1:
                # Groupe refusé (réponse illisible, « NO DATA ») : chacune à part, cette fois
                values = {}
                for it in group:
                    v = self._read([it])
                    values.update(v or {})
                if self.slow and sum(v is not None for v in values.values()) >= 2:
                    # Les mesures répondent seules mais pas ensemble : groupes plus petits ensuite
                    self._shrink(group[0][3], len(group) - 1)
            for key, cmd, conv, maker, *_ in group:
                answered |= self._store(state, key, conv, maker, (values or {}).get(key))
            if delay:
                time.sleep(delay)
        return answered

    def _groups(self, due):
        obd_items = [it for it in due if not it[3]]
        uds_items = [it for it in due if it[3]]
        n_obd, n_uds = self.max_obd, self.max_uds
        return ([obd_items[i:i + n_obd] for i in range(0, len(obd_items), n_obd)]
                + [uds_items[i:i + n_uds] for i in range(0, len(uds_items), n_uds)])

    @staticmethod
    def _store(state, key, conv, maker, value):
        """Range une réponse décodée ; True si le calculateur a répondu."""
        if maker:   # mesure constructeur : valeur brute, étalonnée par state
            state.update_raw(key, value)
            return value is not None
        if value is None:
            state.update(key, None)
            return False
        v = value.magnitude if hasattr(value, "magnitude") else value
        try:
            v = float(conv(v) if conv else v)
        except (TypeError, ValueError):
            state.update(key, None)
            return True
        state.update(key, round(v, 2))
        return True

    # --- Une requête : envoi, découpage de la réponse ---

    def _read(self, group, count=None):
        """{clé: valeur décodée (None : pas de réponse)} pour un groupe de mesures du même service (01 ou 22),
        None si la réponse est illisible (groupe refusé, réponse tronquée)."""
        uds = group[0][3]
        body = b"".join(it[1].command[2:] for it in group)   # PID ou identifiants, sans le service
        req = (b"22" if uds else b"01") + body
        if count is None:
            count = self.count_ok and req not in self.no_count
        n = self.frames.get(req)   # connu après une première réponse complète, sans suffixe
        send = req + (b"%X" % n if count and n and n <= 15 else b"")
        t0 = time.time()
        try:
            messages = self.elm.send_and_parse(send) or []
        except Exception:
            return None
        ms = (time.time() - t0) * 1000
        self.ping_ms = ms if self.ping_ms is None else self.ping_ms + PING_EMA * (ms - self.ping_ms)
        frames = sum(len(m.frames) for m in messages)
        if frames and req not in self.frames:
            self.frames[req] = frames
        data = next((m for m in messages if m.data and m.data[0] == (0x62 if uds else 0x41)), None)
        if data is None:
            if len(group) == 1 or not messages:
                return {it[0]: None for it in group}   # pas de réponse : mesure muette, pas groupe refusé
            return None
        out = self._split_uds(group, data) if uds else self._split_obd(group, data)
        if out is not None and (len(out) == len(group) or len(group) == 1):
            return {it[0]: out.get(it[0]) for it in group}
        if send != req:
            # Réponse tronquée par le suffixe (réponse en plusieurs trames) : plus de suffixe pour celle-ci
            self.no_count.add(req)
            self.frames.pop(req, None)
        elif out:
            # Le calculateur n'a rendu qu'une partie du groupe : groupes ramenés à ce qu'il rend
            self._shrink(uds, len(out))
        return None

    def _shrink(self, uds, n):
        attr = "max_uds" if uds else "max_obd"
        if 1 <= n < getattr(self, attr):
            setattr(self, attr, n)
            print(f"Lecture : le calculateur ne rend que {n} {'mesures ' + self.car.MAKER if uds else 'PID OBD'} par requête,"
                  " groupes réduits")

    @staticmethod
    def _part(msg, data):
        m = Message(msg.frames)
        m.ecu, m.data = msg.ecu, bytearray(data)
        return m

    def _split_obd(self, group, msg):
        """41 PID A B PID A … → {clé: valeur} des PID présents dans la réponse, None si elle est illisible."""
        by_pid = {it[1].pid: it for it in group}
        d, i, out = msg.data, 1, {}
        while i < len(d) and len(out) < len(group):
            it = by_pid.get(d[i])
            if it is None or it[0] in out:
                return None   # octet inattendu : découpage impossible
            n = it[1].bytes - 2
            if i + 1 + n > len(d):
                break   # tronqué : les PID entiers déjà lus comptent
            r = it[1]([self._part(msg, bytes([0x41, d[i]]) + bytes(d[i + 1:i + 1 + n]))])
            out[it[0]] = None if r.is_null() else r.value
            i += 1 + n
        return out

    def _split_uds(self, group, msg):
        """62 DID x x DID y … → {clé: valeur} des identifiants présents, None si la réponse est illisible."""
        fallback = self.car.FALLBACK
        by_did = {bytes.fromhex(fallback[it[0]][0]): it for it in group}
        d, i, out = bytes(msg.data), 1, {}
        while i + 2 <= len(d) and len(out) < len(group):
            did = d[i:i + 2]
            it = by_did.get(did)
            if it is None or it[0] in out:
                return None
            n = fallback[it[0]][5]
            if i + 2 + n > len(d):
                break
            r = it[1]([self._part(msg, b"\x62" + d[i:i + 2 + n])])
            out[it[0]] = None if r.is_null() else r.value
            i += 2 + n
        return out
