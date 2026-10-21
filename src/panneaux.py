"""Limitation de vitesse lue par la caméra de la voiture (reconnaissance des panneaux).

La caméra du pare-brise (IPMA chez Ford) envoie au combiné d'instruments ce qu'elle lit dans un message CAN
diffusé (Ford : Traffic_RecognitnData, 0x3CD, une fois par seconde et à chaque nouveau panneau ; description
d'opendbc, commaai/opendbc ford_lincoln_base_pt.dbc). Ce n'est pas une réponse à une requête : l'ELM327 doit
écouter le bus (ATMA), filtré sur ce seul identifiant (ATCRA), ce qui interrompt la lecture des mesures. D'où une
écoute courte (LISTEN_MAX_S, arrêtée à la première trame) toutes les LISTEN_EVERY_S secondes ; sans rien entendu
GIVE_UP essais de suite (prise OBD derrière une passerelle, autre identifiant), une fois toutes les RETRY_S.

Codage de la limitation : un octet (TsrVLim1MsgTxt_D_Rq), documenté en partie seulement. Gardé tel quel
(« panneau_brut ») ; la limitation en km/h en est déduite : la valeur même (50, 80, 110), ou en dizaines (5 → 50).
"""

import re
import time

LISTEN_EVERY_S = 10     # entre deux écoutes, quand la caméra a déjà été entendue
LISTEN_MAX_S = 1.3      # écoute au plus (le message revient chaque seconde)
GIVE_UP = 6             # écoutes sans rien de suite : on espace
RETRY_S = 300
NO_LIMIT = {0, 251, 252, 253, 254, 255}   # rien à afficher, limitation levée, pas de donnée, défaut


def bits(data, start, length):
    """Signal Motorola (gros-boutiste) d'une trame CAN, numérotation des bits des fichiers DBC."""
    v, bit = 0, start
    for _ in range(length):
        byte, b = divmod(bit, 8)
        v = (v << 1) | ((data[byte] >> b) & 1)
        bit = (byte + 1) * 8 + 7 if b == 0 else bit - 1
    return v


def decode(data):
    """Trame Traffic_RecognitnData → {"raw", "limit" (km/h ou None), "status"}."""
    raw = bits(data, 31, 8)
    status = bits(data, 10, 3)   # 1 éteinte, 2 caméra + navigation, 3 caméra seule, 5 en défaut
    limit = None
    if raw not in NO_LIMIT:
        if 2 <= raw <= 13:
            limit = raw * 10        # codée en dizaines
        elif raw >= 20 and raw % 5 == 0 and raw <= 150:
            limit = raw             # codée telle quelle
    return {"raw": raw, "limit": limit, "status": status}


class SignListener:
    """Écoute du message de la caméra entre deux tours de lecture (acquisition.py)."""

    def __init__(self, conn, can_id):
        self.elm = conn.interface
        self.id = f"{can_id:03X}"
        self.line = re.compile(rf"^{self.id}([0-9A-F]{{16}})$")
        self.next = 0.0
        self.misses = 0
        self.heard = False

    def maybe_listen(self, state):
        now = time.time()
        if now < self.next:
            return
        try:
            frame = self._listen()
        except Exception as e:   # adaptateur qui n'aime pas ATMA : pas d'écoute, la lecture continue
            print(f"Panneaux : écoute impossible ({e})")
            frame = None
        if frame is not None:
            d = decode(frame)
            if not self.heard:
                print(f"Panneaux : caméra entendue ({self.id} {frame.hex(' ').upper()}, limitation brute {d['raw']})")
            self.heard, self.misses, self.next = True, 0, now + LISTEN_EVERY_S
            state.set_sign(d, frame)
        else:
            self.misses += 1
            self.next = now + (LISTEN_EVERY_S if self.misses < GIVE_UP else RETRY_S)
            if self.misses == GIVE_UP:
                print(f"Panneaux : rien entendu sur {self.id} après {GIVE_UP} écoutes, nouvel essai toutes les "
                      f"{RETRY_S // 60} min (prise OBD derrière une passerelle ?)")

    def _listen(self):
        """Écoute ATMA filtrée sur l'identifiant ; renvoie les 8 octets de la première trame, ou None."""
        port = getattr(self.elm, "_ELM327__port", None)
        if port is None:
            return None
        frame = None
        self.elm.send_and_parse(f"ATCRA{self.id}".encode())
        old = port.timeout
        try:
            port.timeout = 0.1
            port.reset_input_buffer()
            port.write(b"ATMA\r")
            port.flush()
            buf, end = b"", time.monotonic() + LISTEN_MAX_S
            while frame is None and time.monotonic() < end:
                buf += port.read(port.in_waiting or 1)
                *lines, buf = re.split(rb"[\r\n]", buf)
                for raw in lines:
                    m = self.line.match(raw.decode("ascii", "ignore").replace(" ", "").upper())
                    if m:
                        frame = bytes.fromhex(m.group(1))
                        break
            # Fin de l'écoute : n'importe quel caractère l'arrête, puis l'invite « > »
            port.write(b"\r")
            port.flush()
            tail, end = b"", time.monotonic() + 1.5
            while b">" not in tail and time.monotonic() < end:
                tail += port.read(port.in_waiting or 1)
        finally:
            port.timeout = old
            self.elm.send_and_parse(b"ATCRA")   # réponses de nouveau filtrées sur le calculateur interrogé
        return frame
