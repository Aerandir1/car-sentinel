"""Lecture rapide du calculateur : moins de requêtes par tour, et des requêtes plus courtes.

Sans ça, chaque mesure est une requête à part (≈ 45 ms aller-retour sur la Fiesta), toutes à chaque tour : avec
une trentaine de mesures, un tour dure 1,3 s (0,8 Hz). Trois leviers, chacun essayé à la connexion et abandonné
si le calculateur ou l'adaptateur ne suit pas :

  priorités   chaque mesure a sa période (PERIOD) : régime, vitesse, pédale… à chaque tour, les températures
              toutes les 2 s, le niveau de carburant toutes les 10 s
  groupage    jusqu'à 6 PID OBD dans une requête (« 01 0C 0D 49 11 0B 04 », permis par l'OBD sur CAN), et
              plusieurs identifiants Ford dans une requête UDS (« 22 033E 0462 »), si le calculateur l'accepte
  adressage   requêtes envoyées au seul calculateur moteur (ATSH 7E0) au lieu de tous (7DF), et nombre de
              réponses attendu ajouté à la requête (« 010C0D1 ») : l'ELM327 rend la main dès la réponse reçue,
              au lieu d'attendre que d'autres calculateurs répondent

Les requêtes passent directement par l'interface ELM327 de python-obd (send_and_parse) ; les réponses sont
découpées mesure par mesure et décodées par les décodeurs habituels (python-obd, ford.py).
"""

import time

from obd.protocols.protocol import Message

# Période de lecture (s) de chaque mesure, 0 : à chaque tour. Les mesures Ford gardent celle de ford.FALLBACK.
# Rapides : ce que suivent les trajets (vitesse, régime, pédale, conso) et ce qui bouge sous le pied.
PERIOD = {
    "rpm": 0, "speed": 0, "pedal": 0, "throttle": 0, "map": 0, "load": 0, "fuel": 0, "timing": 0,
    "maf": 0.5, "lambda": 0.5, "stft": 0.5, "rail": 0.5,
    "coolant": 2, "oil": 2, "intake": 2, "cat": 2, "voltage": 2, "ltft": 2,
    "baro": 5, "ambient": 5, "ethanol": 5, "fuel_lvl": 10,
}
MAX_OBD = 6      # PID par requête OBD (limite de la norme)
MAX_UDS = 4      # identifiants Ford par requête UDS (prudent : la taille de réponse dépend du calculateur)
PING_EMA = 0.1   # lissage du temps de réponse affiché


class Reader:
    """Mesures à lire et façon de les demander, choisie à la connexion (setup), puis lues tour après tour (tick)."""

    def __init__(self, conn, cmds):
        """cmds : (clé, commande, conversion, lecture espacée ou None, requête forcée), comme acquisition.py."""
        self.conn, self.elm = conn, conn.interface
        self.items = []   # [clé, commande, conversion, ford ?, période, dernière lecture]
        for key, cmd, conv, slow, _force in cmds:
            period = slow.period if slow is not None else PERIOD.get(key, 1)
            self.items.append([key, cmd, conv, slow is not None, period, 0.0])
        self.frames = {}        # requête → nombre de trames de la réponse (suffixe « nombre de réponses »)
        self.no_count = set()   # requêtes que le suffixe tronque : envoyées sans
        self.count_ok = False   # l'adaptateur accepte le suffixe
        self.group_obd = self.group_uds = False
        self.ping_ms = None     # temps de réponse moyen d'une requête

    # --- Connexion : ce que l'adaptateur et le calculateur acceptent ---

    def setup(self):
        """Essaie adressage direct, suffixe et groupage ; renvoie un résumé pour le journal."""
        notes = []
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
        if len(probe) == 2:
            self.group_obd = self._answers(probe)
            if self.group_obd:
                notes.append(f"jusqu'à {MAX_OBD} PID OBD par requête")
        if len(uds_items) >= 2:
            self.group_uds = self._answers(uds_items[:2])
            if self.group_uds:
                notes.append(f"jusqu'à {MAX_UDS} mesures Ford par requête")
        return notes

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
        due = [it for it in self.items if now - it[5] >= it[4]]
        for it in due:
            it[5] = now
        answered = False
        for group in self._groups(due):
            values = self._read(group)
            if values is None and len(group) > 1:
                # Groupe refusé (réponse illisible) : chacune à part, cette fois
                values = {}
                for it in group:
                    v = self._read([it])
                    values.update(v or {})
            for key, cmd, conv, ford, *_ in group:
                answered |= self._store(state, key, conv, ford, (values or {}).get(key))
            if delay:
                time.sleep(delay)
        return answered

    def _groups(self, due):
        obd_items = [it for it in due if not it[3]]
        uds_items = [it for it in due if it[3]]
        n_obd = MAX_OBD if self.group_obd else 1
        n_uds = MAX_UDS if self.group_uds else 1
        return ([obd_items[i:i + n_obd] for i in range(0, len(obd_items), n_obd)]
                + [uds_items[i:i + n_uds] for i in range(0, len(uds_items), n_uds)])

    @staticmethod
    def _store(state, key, conv, ford, value):
        """Range une réponse décodée ; True si le calculateur a répondu."""
        if ford:   # mesure Ford : valeur brute, étalonnée par state
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
        values = self._split_uds(group, data) if uds else self._split_obd(group, data)
        if values is None and send != req:
            # Réponse tronquée par le suffixe (réponse en plusieurs trames) : plus de suffixe pour celle-ci
            self.no_count.add(req)
            self.frames.pop(req, None)
        return values

    @staticmethod
    def _part(msg, data):
        m = Message(msg.frames)
        m.ecu, m.data = msg.ecu, bytearray(data)
        return m

    def _split_obd(self, group, msg):
        """41 PID A B PID A … → une réponse par PID, décodée par la commande python-obd."""
        by_pid = {it[1].pid: it for it in group}
        d, i, out = msg.data, 1, {}
        while i < len(d) and len(out) < len(group):
            it = by_pid.get(d[i])
            if it is None or it[0] in out:
                return None   # octet inattendu : découpage impossible
            n = it[1].bytes - 2
            if i + 1 + n > len(d):
                return None   # tronqué
            r = it[1]([self._part(msg, bytes([0x41, d[i]]) + bytes(d[i + 1:i + 1 + n]))])
            out[it[0]] = None if r.is_null() else r.value
            i += 1 + n
        return {it[0]: out.get(it[0]) for it in group}

    def _split_uds(self, group, msg):
        """62 DID x x DID y … → une réponse par identifiant, décodée par ford.py."""
        import ford
        by_did = {bytes.fromhex(ford.FALLBACK[it[0]][0]): it for it in group}
        d, i, out = bytes(msg.data), 1, {}
        while i + 2 <= len(d) and len(out) < len(group):
            did = d[i:i + 2]
            it = by_did.get(did)
            if it is None or it[0] in out:
                return None
            n = ford.FALLBACK[it[0]][5]
            if i + 2 + n > len(d):
                return None
            r = it[1]([self._part(msg, b"\x62" + d[i:i + 2 + n])])
            out[it[0]] = None if r.is_null() else r.value
            i += 2 + n
        if len(group) > 1 and not out:
            return None
        return {it[0]: out.get(it[0]) for it in group}
