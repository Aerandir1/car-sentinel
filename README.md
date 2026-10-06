# Clio Sentinel

Dashboard OBD temps réel pour la Clio IV dCi 90 (moteur K9K), sur PC portable, via un adaptateur ELM327
branché sur la prise OBD. `dashboard_clio.py` lit l'ECU et sert le dashboard sur https://127.0.0.1:8080
(fonctionne hors ligne), plus une API JSON (`/api/state`, `/api/meta`, `/api/settings`).

Autour :
- [clio-hub](https://github.com/Aerandir1/clio-hub) : la page d'accueil 3D qui réunit les services ; son bouton
  « Mode dashboard » ouvre ce dashboard et elle affiche les valeurs en direct lues sur `/api/state`
  (autorisé pour les pages servies depuis 127.0.0.1 / localhost) ;
- [clio-autoradio](https://github.com/Aerandir1/clio-autoradio) : radio, navigation, CarPlay, Spotify (en pause) ;
- le scan approfondi du calculateur (inventaire des capteurs, UDS, diagnostic) : repo à part.

```
dashboard_clio.py     point d'entrée : options, lancement de l'acquisition et du serveur
etat.py               liste des métriques (METRICS), état partagé, CSV
acquisition.py        lecture de l'ECU par l'ELM327
serveur.py            serveur HTTP : page, logos, API JSON
certs.py              certificat HTTPS (mkcert ou auto-signé)
donnees.py            enregistrement des mesures horodatées (SQLite)
web/dashboard.html    le dashboard
data/                 les bases SQLite (pas dans git)
lancer.sh             lancement en local (venv + navigateur)
Dockerfile, docker-compose*.yml
```

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

ELM327 USB branché, contact mis :

```bash
docker compose down                                                   # si un ancien conteneur tourne encore
OBD_GID=$(stat -c %g /dev/ttyUSB0) docker compose up --build          # puis https://127.0.0.1:8080
```

`OBD_GID` donne au conteneur le droit d'ouvrir le port série (sous Arch, il appartient au groupe `uucp` ;
par défaut 20 = dialout, celui de WSL / Debian / Ubuntu). `OBD_DEVICE=/dev/ttyACM0` si la clé n'apparaît pas
en `ttyUSB0`. Autres options du script : `CLIO_ARGS="--port /dev/ttyUSB0 --log logs/trajet.csv"`
(PowerShell : `$env:CLIO_ARGS="…"`).

Le certificat est rangé dans `certs/` et les CSV dans `logs/` (`--log logs/trajet.csv`). Le navigateur
tourne sous Windows, hors du conteneur : rien de spécial côté Docker. Le conteneur n'a pas
mkcert : soit on accepte l'avertissement, soit on importe `certs/selfsigned.pem` dans Windows (*Autorités de
certification racines de confiance*).

**ELM327 depuis le conteneur.** Docker Desktop ne voit pas les ports COM de Windows. Selon l'adaptateur :
- **Wi-Fi** : PC connecté au Wi-Fi de l'adaptateur, aucune clé à transmettre (`OBD_DEVICE=/dev/null`) :
  `OBD_DEVICE=/dev/null CLIO_ARGS="--port socket://192.168.0.10:35000 --baud 38400" docker compose up`
- **USB** : passer la clé à WSL2 avec [usbipd-win](https://github.com/dorssel/usbipd-win), en PowerShell admin :
  ```powershell
  winget install usbipd
  usbipd list                          # repérer le BUSID de l'ELM327 (CH340, FTDI…)
  usbipd bind --busid 2-3              # une seule fois
  usbipd attach --wsl --busid 2-3      # à refaire à chaque branchement
  ```
  puis `docker compose up --build`.
- **Bluetooth** (port COM virtuel) : passer par un pont RFC 2217 sous Windows (ex. `rfc2217_server.py` des
  exemples de pyserial), puis `--port rfc2217://host.docker.internal:2217`.

## Utilisation

```bash
python dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv  # dans la voiture, contact mis
```

| Option | Rôle |
|---|---|
| `--port` | port série de l'ELM327 (auto-détection si absent) |
| `--db FICHIER.db` | base des mesures (défaut `data/clio.db`) |
| `--no-db` | n'enregistre rien |
| `--log FICHIER.csv` | enregistre les mesures en CSV |
| `--log-hz` | fréquence du CSV (défaut 5 Hz) |
| `--delay` | pause entre deux requêtes OBD (si l'ELM327 décroche) |
| `--http` | port de la page web (défaut 8080) |
| `--no-https` | sert la page en HTTP simple, sans certificat |
| `--bind` | adresse d'écoute (défaut 127.0.0.1 ; 0.0.0.0 dans Docker) |
| `--fast` | mode rapide python-obd : requêtes plus courtes (certains clones ELM327 le refusent) |
| `--max-hz` | fréquence max d'acquisition (défaut 20) |

L'onglet **Paramètres** du dashboard règle tout ça à chaud :
- affichage (gardé dans le navigateur) : rafraîchissement de la page, durée du graphique ;
- acquisition (appliqué par le script jusqu'à son arrêt) : fréquence max, pause entre requêtes OBD, fréquence du CSV,
  mode rapide (le basculer relance la connexion à l'ELM327).

Au démarrage, le script demande au calculateur quels PID il supporte et n'affiche que ceux-là.
Pour alléger ou compléter les mesures, édite la liste `METRICS` en haut de `etat.py`.

## Courbes

Chaque tuile porte une mini-courbe des 2 dernières minutes (durée réglable dans Paramètres), sauf la pression
atmosphérique et la température extérieure, presque constantes. Un clic sur une tuile ouvre sa courbe détaillée :
1 min, 5 min, 15 min ou trajet entier, mise à jour en direct sur le trajet en cours, et une liste pour revoir les
trajets passés enregistrés dans la base. Le curseur donne la valeur et l'heure exactes ; les seuils d'alerte de la
métrique sont tracés quand ils tombent dans l'échelle. La courbe est coupée là où le calculateur n'a pas répondu.
Échap ou un clic à côté pour fermer.

## Données

Chaque valeur lue est enregistrée avec son horodatage dans une base SQLite (`data/clio.db`).
L'ELM327 interroge les PID l'un après l'autre : chaque valeur porte
l'instant exact où le calculateur a répondu, pas l'heure de la boucle. Une non-réponse est gardée (valeur vide) :
on sait quand un capteur a décroché. La suralimentation calculée prend l'horodatage de la pression d'admission.
Écriture groupée une fois par seconde, environ 3 Mo par heure de route.

| Table | Colonnes |
|---|---|
| `trips` | `id`, `start_ms`, `end_ms`, `source` : un lancement du script = un trajet |
| `samples` | `trip_id`, `t_ms` (ms depuis 1970, UTC), `key` (`rpm`, `coolant`… comme `METRICS`, plus `boost`), `value` |

```bash
sqlite3 data/clio.db "SELECT datetime(t_ms/1000, 'unixepoch', 'localtime'), key, value
                      FROM samples WHERE key = 'coolant' ORDER BY t_ms DESC LIMIT 10"
```

API : `/api/trips` (liste des trajets), `/api/history?key=rpm[&trip=ID][&from=MS&to=MS]` (points `[t_ms, valeur]`
du dernier trajet par défaut, moyennés par tranches au-delà de 2000 points). `/api/state` donne aussi `ts`,
l'horodatage de la dernière valeur de chaque métrique.

## Limites

- Avec un ELM327, les PID sont interrogés l'un après l'autre : quelques Hz au total.
- La température d'huile et la pression de rail sont rarement dispo en OBD standard chez Renault
  (mesures constructeur en UDS : voir le repo de scan du calculateur).
