# Clio Sentinel

Outil de diagnostic et de maintenance préventive pour la Clio IV dCi 90 (moteur K9K), sur PC portable,
via un adaptateur ELM327 branché sur la prise OBD. Objectif : un statut pour chaque organe de la voiture
et l'anticipation des pannes. Voir [ROADMAP.md](ROADMAP.md).
Le script lit l'ECU et sert une page locale sur https://127.0.0.1:8080 (fonctionne hors ligne).

- `accueil.html` : page d'accueil 3D (Three.js, dans `vendor/`) avec la Clio, la fiche technique et les infos en direct.
  Le bouton « Mode dashboard » (ou Entrée) fait entrer la caméra dans la voiture jusqu'au combiné, puis affiche le dashboard.
  « ↩ Accueil » (ou Échap) fait le chemin inverse.
  « Rouler » (ou Espace) lance une balade de nuit façon Need for Speed : route qui défile, lampadaires,
  changements de file, caméras de replay, nitro et compteur. « Garage » ramène au plateau tournant.
- `dashboard.html` : le dashboard temps réel (aussi accessible directement sur `/dashboard`).
- `autoradio.html` : l'autoradio (aussi sur `/autoradio`). Bouton « Autoradio » (ou R) sur l'accueil :
  la caméra entre jusqu'à l'écran central, qui s'allume, puis l'autoradio s'affiche. Écrans : accueil
  (heure, valeurs OBD, source en cours), radio (webradios Radio France, Internet requis), CarPlay, véhicule.
  Molette de volume à la souris (ou + / −), Échap pour revenir.

## Navigation et Spotify (autoradio)

Tout se règle dans l'écran **Paramètres** de l'autoradio (icône engrenage). Internet est nécessaire pour les cartes,
les itinéraires et Spotify (partage de connexion de l'iPhone par exemple).

- **Navigation** (`navigation.js`) : recherche d'adresse (Nominatim), itinéraire (OSRM), puis guidage :
  carte qui suit la voiture en 3D, consignes en français (« Dans 300 m, tournez à droite »), heure d'arrivée,
  recalcul si on sort de la route. Carte **OpenStreetMap** (MapLibre + OpenFreeMap, sans clé) par défaut, ou
  **Google Maps** avec trafic si une clé API (*Maps JavaScript API*) est renseignée. Le bouton « Ouvrir ↗ »
  envoie la destination vers Google Maps ou Waze. Sans GPS, « Démarrer » lance une simulation qui roule sur
  l'itinéraire à la vitesse lue sur l'OBD (50 km/h si l'OBD ne répond pas).
- **Spotify** (compte Premium pour piloter la lecture) :
  1. sur <https://developer.spotify.com/dashboard> : *Create app*, cocher **Web API** et **Web Playback SDK** ;
  2. *Redirect URI* : `https://127.0.0.1:8080/spotify-callback` (copiable depuis Paramètres) ;
  3. recopier le *Client ID* dans Paramètres, puis « Se connecter ».

  Le PC apparaît alors comme appareil Spotify Connect « Clio » ; on peut aussi piloter la lecture en cours
  sur l'iPhone et lancer ses playlists. La page s'ouvre sur `127.0.0.1` (le script y redirige `localhost`),
  seule adresse locale acceptée par Spotify.

## GPS

Le PC n'a pas de GPS : le script lit la position et la sert sur `/api/gps`, que la navigation utilise.

```bash
python dashboard_clio.py --gps /dev/ttyACM0                 # clé GPS USB (u-blox…), phrases NMEA
python dashboard_clio.py --gps tcp:172.20.10.1:11123        # iPhone : app GPS2IP, mode « TCP Server »,
                                                            # PC connecté au partage de connexion de l'iPhone
python dashboard_clio.py --gps gpsd                         # si gpsd tourne déjà (localhost:2947)
```

La pastille en haut à droite de la carte indique la source (GPS, GPS iPhone, Simulation) et le nombre de satellites.
Une position de plus de 5 s est ignorée ; le déplacement est lissé entre deux positions (1 par seconde).

## CarPlay

Vrai CarPlay via un dongle USB **Carlinkit CPC200-CCPA** (ou Autokit), piloté par
[node-carplay](https://github.com/rhysmorgan134/node-CarPlay) (MIT, empaqueté dans `vendor/carplay.js`).
Les dongles « filaire → sans-fil » pour voitures déjà équipées CarPlay ne marchent **pas**.

1. Autoriser le dongle pour ton utilisateur (une seule fois) :
   ```bash
   sudo cp udev/70-carlinkit.rules /etc/udev/rules.d/
   sudo udevadm control --reload && sudo udevadm trigger
   ```
2. Ouvrir la page dans **Chromium ou Chrome** (WebUSB + WebCodecs ; Firefox ne gère pas WebUSB).
3. Autoradio → CarPlay → « Connecter le dongle » et le choisir. Ensuite il se reconnecte tout seul.
4. Brancher l'iPhone au dongle par câble, ou l'appairer en Bluetooth (« Clio ») pour le sans-fil.

L'image et le son passent par le PC ; l'écran se pilote à la souris ou au tactile.
Le micro n'est pas encore relié : Siri et les appels n'entendent pas ta voix.

- `logo/` : logos Renault en SVG (servis sur `/logo/…`). Les pages s'en servent comme masque, donc la couleur
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
CLIO_ARGS="--gps tcp:172.20.10.1:11123" docker compose up   # autres options du script (PowerShell : $env:CLIO_ARGS="…")
```

Le certificat est rangé dans `certs/` et les CSV dans `logs/` (`--log logs/trajet.csv`). Le navigateur et
CarPlay (WebUSB) tournent sous Windows, hors du conteneur : rien de spécial côté Docker. Le conteneur n'a pas
mkcert : soit on accepte l'avertissement, soit on importe `certs/selfsigned.pem` dans Windows (*Autorités de
certification racines de confiance*).

**ELM327 depuis le conteneur.** Docker Desktop ne voit pas les ports COM de Windows. Selon l'adaptateur :
- **Wi-Fi** : rien à transmettre, `CLIO_ARGS="--port socket://192.168.0.10:35000" docker compose up`
  (PC connecté au Wi-Fi de l'adaptateur).
- **USB** : passer la clé à WSL2 avec [usbipd-win](https://github.com/dorssel/usbipd-win), en PowerShell admin :
  ```powershell
  winget install usbipd
  usbipd list                          # repérer le BUSID de l'ELM327 (CH340, FTDI…)
  usbipd bind --busid 2-3              # une seule fois
  usbipd attach --wsl --busid 2-3      # à refaire à chaque branchement
  ```
  puis `docker compose -f docker-compose.yml -f docker-compose.obd.yml up --build`
  (`OBD_DEVICE=/dev/ttyACM0` si la clé n'apparaît pas en `ttyUSB0`). Même méthode pour une clé GPS USB.
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
- La température d'huile et la pression de rail sont rarement dispo en OBD standard chez Renault.
  Étape suivante : requêtes UDS (mode 22) en s'appuyant sur les définitions de DDT4all.

## Modèle 3D de la Clio (optionnel)

Dépose un modèle glTF dans `models/clio.glb` (ou `models/clio/scene.gltf` + ses fichiers) : il remplace la
Clio dessinée en code au prochain rechargement de la page. Réglages (orientation, matériaux repeints, crédit)
dans la constante `MODEL` de `accueil.html`. La console du navigateur liste les matériaux trouvés.

Modèle actuel : « renault clio r.s. 200 edc » par amogusstrikesback2 (Sketchfab, CC BY 4.0, voir `models/clio/license.txt`).
Le crédit est affiché en bas à droite de la page d'accueil, comme l'exige la licence.
