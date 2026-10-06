# Clio Sentinel

Les outils embarqués pour la Clio IV dCi 90 (moteur K9K), sur PC portable : dashboard OBD temps réel
(adaptateur ELM327 sur la prise OBD) et sa page d'accueil 3D.
Ailleurs : le scan approfondi du calculateur (inventaire des capteurs, UDS, diagnostic) dans un repo à part,
et l'autoradio (radio, navigation, CarPlay, Spotify), en pause, dans
[clio-autoradio](https://github.com/Aerandir1/clio-autoradio).

`dashboard_clio.py` lit l'ECU et sert les pages de `web/` sur https://127.0.0.1:8080 (fonctionne hors ligne).

```
dashboard_clio.py     serveur : lecture OBD, API JSON, pages web
web/                  tout ce que sert le navigateur
  accueil.html          page d'accueil 3D
  dashboard.html        dashboard temps réel
  vendor/               Three.js
  models/ logo/         modèle 3D de la Clio, logos Renault
lancer.sh             lancement en local (venv + navigateur)
Dockerfile, docker-compose*.yml
```

- `accueil.html` : page d'accueil 3D (Three.js, dans `web/vendor/`) avec la Clio, la fiche technique et les infos en direct.
  Le bouton « Mode dashboard » (ou Entrée) fait entrer la caméra dans la voiture jusqu'au combiné, puis affiche le dashboard.
  « ↩ Accueil » (ou Échap) fait le chemin inverse.
- `dashboard.html` : le dashboard temps réel, aussi accessible directement sur `/dashboard`.
- `web/logo/` : logos Renault en SVG (servis sur `/logo/…`). Les pages s'en servent comme masque, donc la couleur
  se règle en CSS (`color` sur `.r-mark` / `.r-lockup`) sans retoucher les fichiers.

## Installation (Arch)

```bash
sudo usermod -aG uucp $USER          # accès à /dev/ttyUSB0, puis déconnexion/reconnexion
python -m venv ~/.venvs/obd && source ~/.venvs/obd/bin/activate
pip install -r requirements.txt
```

## HTTPS

La page est servie en **https://127.0.0.1:8080**. Au premier lancement, le script crée un certificat dans `certs/` :
- si **mkcert** est installé, le certificat est reconnu par le navigateur (aucun avertissement) :
  ```bash
  sudo pacman -S mkcert nss && mkcert -install    # une seule fois, puis relancer le script
  ```
- sinon, certificat auto-signé : Chromium affiche un avertissement, « Paramètres avancés » → « Continuer ».

`--no-https` repasse en HTTP simple. Supprimer `certs/` pour régénérer le certificat.

## Docker (Docker Desktop Windows, macOS, Linux)

```bash
docker compose up --build                  # mode démo → https://127.0.0.1:8080
CLIO_ARGS="--port socket://192.168.0.10:35000 --baud 38400" docker compose up   # autres options du script (PowerShell : $env:CLIO_ARGS="…")
```

Le certificat est rangé dans `certs/` et les CSV dans `logs/` (`--log logs/trajet.csv`). Le navigateur
tourne sous Windows, hors du conteneur : rien de spécial côté Docker. Le conteneur n'a pas
mkcert : soit on accepte l'avertissement, soit on importe `certs/selfsigned.pem` dans Windows (*Autorités de
certification racines de confiance*).

**ELM327 depuis le conteneur.** Docker Desktop ne voit pas les ports COM de Windows. Selon l'adaptateur :
- **Wi-Fi** : rien à transmettre, `CLIO_ARGS="--port socket://192.168.0.10:35000 --baud 38400" docker compose up`
  (PC connecté au Wi-Fi de l'adaptateur).
- **USB** : passer la clé à WSL2 avec [usbipd-win](https://github.com/dorssel/usbipd-win), en PowerShell admin :
  ```powershell
  winget install usbipd
  usbipd list                          # repérer le BUSID de l'ELM327 (CH340, FTDI…)
  usbipd bind --busid 2-3              # une seule fois
  usbipd attach --wsl --busid 2-3      # à refaire à chaque branchement
  ```
  puis `docker compose -f docker-compose.yml -f docker-compose.obd.yml up --build`
  (`OBD_DEVICE=/dev/ttyACM0` si la clé n'apparaît pas en `ttyUSB0`).
  Sous Linux, le conteneur tourne sous ton utilisateur : donner le groupe du port série avec
  `OBD_GID=$(stat -c %g /dev/ttyUSB0)` (par défaut 20 = dialout, celui de WSL / Debian / Ubuntu).
- **Bluetooth** (port COM virtuel) : passer par un pont RFC 2217 sous Windows (ex. `rfc2217_server.py` des
  exemples de pyserial), puis `--port rfc2217://host.docker.internal:2217`.

## Utilisation

```bash
python dashboard_clio.py --demo                              # simulation d'un trajet dCi, sans voiture
python dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv  # dans la voiture, contact mis
```

| Option | Rôle |
|---|---|
| `--port` | port série de l'ELM327 (auto-détection si absent) |
| `--log FICHIER.csv` | enregistre les mesures en CSV |
| `--log-hz` | fréquence du CSV (défaut 5 Hz) |
| `--delay` | pause entre deux requêtes OBD (si l'ELM327 décroche) |
| `--http` | port de la page web (défaut 8080) |
| `--no-https` | sert la page en HTTP simple, sans certificat |
| `--bind` | adresse d'écoute (défaut 127.0.0.1 ; 0.0.0.0 dans Docker) |
| `--fast` | mode rapide python-obd : requêtes plus courtes (certains clones ELM327 le refusent) |
| `--max-hz` | fréquence max d'acquisition (défaut 10 en démo, 20 sinon) |

L'onglet **Paramètres** du dashboard règle tout ça à chaud :
- affichage (gardé dans le navigateur) : rafraîchissement de la page, durée du graphique ;
- acquisition (appliqué par le script jusqu'à son arrêt) : fréquence max, pause entre requêtes OBD, fréquence du CSV,
  mode rapide (le basculer relance la connexion à l'ELM327).

Au démarrage, le script demande au calculateur quels PID il supporte et n'affiche que ceux-là.
Pour alléger ou compléter les mesures, édite la liste `METRICS` en haut de `dashboard_clio.py`.

## Limites

- Avec un ELM327, les PID sont interrogés l'un après l'autre : quelques Hz au total.
- La température d'huile et la pression de rail sont rarement dispo en OBD standard chez Renault
  (mesures constructeur en UDS : voir le repo de scan du calculateur).

## Modèle 3D de la Clio (optionnel)

Dépose un modèle glTF dans `web/models/clio.glb` (ou `web/models/clio/scene.gltf` + ses fichiers) : il remplace la
Clio dessinée en code au prochain rechargement de la page. Réglages (orientation, matériaux repeints, crédit)
dans la constante `MODEL` de `web/accueil.html`. La console du navigateur liste les matériaux trouvés.

Modèle actuel : « renault clio r.s. 200 edc » par amogusstrikesback2 (Sketchfab, CC BY 4.0, voir `web/models/clio/license.txt`).
Le crédit est affiché en bas à droite de la page d'accueil, comme l'exige la licence.
