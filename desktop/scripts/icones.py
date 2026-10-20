#!/usr/bin/env python3
"""Icônes de l'appli Windows, dessinées d'après celle de l'appli Android (le compte-tours, ic_compteur.xml) :
écrites dans desktop/icones/ (dans git : à relancer seulement si le dessin change). Linux : rsvg-convert et
ImageMagick (magick).

- appli.ico : l'icône du programme et de l'installeur, comme sur Android (fond nuit, aiguille bleue, zone rouge) ;
- <voiture>-<serveur>.ico : l'icône de la zone de notification, une par état. L'arc du compte-tours prend la couleur
  de la liaison avec la voiture, la pastille en bas à droite celle du serveur (voir ETATS dans main.js).
"""

import subprocess
import tempfile
from pathlib import Path

ICONES = Path(__file__).resolve().parent.parent / "icones"
FOND = "#0B0F14"
TAILLES = [16, 20, 24, 32, 40, 48, 64, 256]

# Liaison avec la voiture → couleur de l'arc
VOITURE = {
    "arret": "#E5484D",    # le service ne répond pas
    "absent": "#7D8796",   # pas d'ELM327
    "elm": "#F5A524",      # ELM327 branché, calculateur muet
    "ok": "#30C46C",       # le calculateur répond
    "demo": "#2D6CDF",     # mode démo
}
# Serveur → pastille (None : pas de pastille, aucun serveur réglé)
SERVEUR = {"aucun": None, "ok": "#30C46C", "ko": "#E5484D", "attente": "#7D8796"}

# Compte-tours sur 32 × 32 : centre (16, 17.5), rayon 10, ouvert en bas
ARC = "M8.93,24.57 A10,10 0 1,1 23.07,24.57"


def svg(arc, aiguille="#FFFFFF", zone=None, pastille=None):
    rouge = f'<path d="M22.5,10.2 A10,10 0 0,1 23.07,24.57" fill="none" stroke="{zone}" stroke-width="4"' \
            f' stroke-linecap="round"/>' if zone else ""
    point = f'<circle cx="26" cy="26" r="5.5" fill="{pastille}" stroke="{FOND}" stroke-width="2"/>' if pastille else ""
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">
<rect x="0" y="0" width="32" height="32" rx="7" fill="{FOND}"/>
<path d="{ARC}" fill="none" stroke="{arc}" stroke-width="4" stroke-linecap="round"/>{rouge}
<path d="M16,17.5 L21.5,11.5" stroke="{aiguille}" stroke-width="3" stroke-linecap="round"/>
<circle cx="16" cy="17.5" r="2.6" fill="#FFFFFF"/>{point}
</svg>"""


def ico(source, nom, tailles=TAILLES, taille_png=256):
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "i.svg"
        f.write_text(source)
        pngs = []
        for t in tailles:
            png = Path(tmp) / f"{t}.png"
            subprocess.run(["rsvg-convert", "-w", str(t), "-h", str(t), "-o", png, f], check=True)
            pngs.append(png)
        subprocess.run(["magick", *pngs, ICONES / f"{nom}.ico"], check=True)
        # Aussi en PNG : fenêtre et notifications (appli), zone de notification hors Windows (npm start sous Linux)
        subprocess.run(["rsvg-convert", "-w", str(taille_png), "-h", str(taille_png), "-o", ICONES / f"{nom}.png", f], check=True)


def main():
    ICONES.mkdir(exist_ok=True)
    ico(svg("#FFFFFF", aiguille="#2D6CDF", zone="#E5484D"), "appli")
    for v, arc in VOITURE.items():
        for s, point in SERVEUR.items():
            ico(svg(arc, pastille=point), f"{v}-{s}", [16, 20, 24, 32, 40, 48, 64], taille_png=32)
    print(f"Icônes écrites dans {ICONES}")


if __name__ == "__main__":
    main()
