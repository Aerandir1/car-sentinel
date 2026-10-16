"""Adresses de la page sur ce PC et sur le réseau."""

import json
import socket
import subprocess
import sys

# Nom de la page : les navigateurs envoient tout nom en .localhost vers la machine elle-même (pas de fichier hosts)
HOST = "clio-sentinel.localhost"
VIRTUAL_IFACES = ("docker", "br-", "veth", "virbr")   # réseaux internes de Docker / libvirt : injoignables du réseau


def lan_names():
    """Noms sous lesquels les autres appareils du réseau voient cette machine : <nom>.local (mDNS : avahi sous
    Linux, intégré à Windows 10 et plus) et ses adresses IPv4."""
    host = socket.gethostname().split(".")[0]
    names = [f"{host}.local"]
    try:
        if sys.platform == "win32":
            # Pas de commande ip : les adresses du nom de la machine (sans les adresses automatiques 169.254)
            names += [a[4][0] for a in socket.getaddrinfo(host, None, socket.AF_INET)
                      if not a[4][0].startswith(("127.", "169.254."))]
        else:
            out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5)
            names += [a["local"] for i in json.loads(out.stdout or "[]") for a in i.get("addr_info", [])
                      if a.get("scope") == "global" and not i.get("ifname", "").startswith(VIRTUAL_IFACES)]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return list(dict.fromkeys(names))
