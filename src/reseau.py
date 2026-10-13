"""Adresses de la page sur ce PC et sur le réseau."""

import json
import socket
import subprocess
from pathlib import Path

# Nom de la page : les navigateurs envoient tout nom en .localhost vers la machine elle-même (pas de /etc/hosts)
HOST = "fiesta-sentinel.localhost"
VIRTUAL_IFACES = ("docker", "br-", "veth", "virbr")   # réseaux internes de Docker / libvirt : injoignables du réseau


def lan_names():
    """Noms sous lesquels les autres appareils du réseau voient cette machine : <nom>.local (mDNS, avahi)
    et ses adresses IPv4."""
    if Path("/.dockerenv").exists():   # dans un conteneur, nom et IP sont ceux du conteneur : inutiles
        return []
    names = [f"{socket.gethostname().split('.')[0]}.local"]
    try:
        out = subprocess.run(["ip", "-j", "-4", "addr", "show", "up"], capture_output=True, text=True, timeout=5)
        names += [a["local"] for i in json.loads(out.stdout or "[]") for a in i.get("addr_info", [])
                  if a.get("scope") == "global" and not i.get("ifname", "").startswith(VIRTUAL_IFACES)]
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    return list(dict.fromkeys(names))
