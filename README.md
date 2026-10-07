# Clio Sentinel

Dashboard OBD temps réel pour la Clio IV dCi 90 (moteur K9K), sur PC portable, via un adaptateur ELM327
branché sur la prise OBD. `src/dashboard_clio.py` lit l'ECU et sert le dashboard sur
https://clio-sentinel.localhost:8080
(fonctionne hors ligne), plus une API JSON (`/api/state`, `/api/meta`, `/api/settings`).

Autour :
- [clio-hub](https://github.com/Aerandir1/clio-hub) : la page d'accueil 3D qui réunit les services ; son bouton
  « Mode dashboard » ouvre ce dashboard et elle affiche les valeurs en direct lues sur `/api/state`
  (autorisé pour les pages servies depuis 127.0.0.1 / localhost) ;
- [clio-autoradio](https://github.com/Aerandir1/clio-autoradio) : radio, navigation, CarPlay, Spotify (en pause) ;
- [clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) : le serveur de la maison qui reçoit
  les mesures et trajets et garde tout l'historique (voir « Serveur ») ;
- le scan approfondi du calculateur (inventaire des capteurs, UDS, diagnostic) : repo à part.

```
src/
  dashboard_clio.py   point d'entrée : options, lancement de l'acquisition et du serveur
  etat.py             liste des métriques (METRICS), état partagé, CSV
  acquisition.py      lecture de l'ECU par l'ELM327
  serveur.py          serveur HTTP : page, logos, API JSON
  certs.py            certificat HTTPS (mkcert ou auto-signé)
  donnees.py          base SQLite : sessions, mesures horodatées, trajets, événements
  trajets.py          découpage des mesures en trajets, fiche de chaque trajet
  synchro.py          envoi de la base vers clio-sentinel-server
web/dashboard.html    le dashboard
data/                 les bases SQLite (pas dans git)
lancer.sh             lancement en local (venv + navigateur)
service.sh            enregistrement permanent : service systemd lancé avec la session
Dockerfile, docker-compose.yml
```

## Installation (Arch)

```bash
sudo usermod -aG uucp $USER          # accès à /dev/ttyUSB0, puis déconnexion/reconnexion
python -m venv ~/.venvs/obd && source ~/.venvs/obd/bin/activate
pip install -r requirements.txt
```

## HTTPS

La page est servie en **https://clio-sentinel.localhost:8080**. Chromium et Firefox envoient tout nom en
`.localhost` vers l'ordi lui-même : rien à mettre dans `/etc/hosts`. https://127.0.0.1:8080 et
https://localhost:8080 redirigent vers ce nom (l'API, elle, répond aussi sur 127.0.0.1, pour le hub).
Au premier lancement, le script crée un certificat dans `certs/` :
- si **mkcert** est installé, le certificat est reconnu par le navigateur (aucun avertissement) :
  ```bash
  sudo pacman -S mkcert nss && mkcert -install    # une seule fois, puis relancer le script
  ```
- sinon, certificat auto-signé : Chromium affiche un avertissement, « Paramètres avancés » → « Continuer ».

`--no-https` repasse en HTTP simple. Supprimer `certs/` pour régénérer le certificat (un certificat sans le nom
`clio-sentinel.localhost` est refait tout seul).

## Docker sous Linux

```bash
docker compose down                                                   # si un ancien conteneur tourne encore
OBD_GID=$(stat -c %g /dev/ttyUSB0) docker compose up --build          # puis https://clio-sentinel.localhost:8080
```

Le conteneur démarre même sans la clé : il voit le `/dev` de l'hôte en lecture seule (sous `/hostdev`) et
attend que l'ELM327 apparaisse. Seuls les ports série USB lui sont autorisés, pas les disques ni le reste.
`OBD_GID` lui donne le droit d'ouvrir le port série (sous Arch il appartient au groupe `uucp` ; par défaut 20 =
dialout, celui de Debian / Ubuntu / WSL). Clé en `ttyACM0` : `CLIO_ARGS="--port /hostdev/ttyACM0"`.

Le certificat est rangé dans `certs/`, la base dans `data/`, les CSV dans `logs/`. Le conteneur n'a pas mkcert :
le navigateur affiche un avertissement à accepter une fois. Avec `restart: unless-stopped`, le conteneur se
relance tout seul au démarrage de Docker : c'est l'équivalent de `service.sh`.

## Docker sous Windows (Docker Desktop)

Tout se fait dans PowerShell, dans le dossier du projet. Docker Desktop ne voit pas les ports COM de Windows :
l'adaptateur passe soit par le Wi-Fi, soit par WSL2.

**1. Une seule fois.** Dans Docker Desktop, *Settings → General* : cocher « Start Docker Desktop when you sign
in ». Le conteneur se relancera alors tout seul à chaque démarrage de Windows.

**2. La base dans un volume Docker.** Un dossier Windows (`C:\...`) partagé avec le conteneur est lent et
SQLite peut y renvoyer « database is locked ». La base va donc dans un volume Docker (`CLIO_DATA=clio-data`).
Pour en sortir une copie (pour l'ouvrir dans DB Browser for SQLite, par exemple) :

```powershell
docker compose stop
docker compose cp clio:/app/data .\data-copie     # clio.db + ses fichiers -wal et -shm : garder les trois ensemble
docker compose start
```

Variante : cloner le projet dans WSL (`\\wsl$\Ubuntu\home\...`) et suivre la section Linux.

**3. Lancer, selon l'adaptateur.**

- **Wi-Fi** (PC connecté au Wi-Fi de l'adaptateur) :
  ```powershell
  $env:CLIO_DATA="clio-data"
  $env:CLIO_ARGS="--port socket://192.168.0.10:35000 --baud 38400"
  $env:CLIO_VERSION=(git describe --always)
  docker compose up -d --build
  ```
- **USB** : passer la clé à WSL2 avec [usbipd-win](https://github.com/dorssel/usbipd-win), en PowerShell admin :
  ```powershell
  winget install usbipd
  usbipd list                                        # repérer le BUSID de l'ELM327 (CH340, FTDI…)
  usbipd bind --busid 2-3                            # une seule fois
  usbipd attach --wsl --busid 2-3 --auto-attach      # laisse la fenêtre ouverte : rattache la clé à chaque branchement
  ```
  puis, dans un autre PowerShell :
  ```powershell
  $env:CLIO_DATA="clio-data"
  $env:CLIO_VERSION=(git describe --always)
  docker compose up -d --build
  ```
  Clé en `ttyACM0` : ajouter `$env:CLIO_ARGS="--port /hostdev/ttyACM0"`.
- **Bluetooth** (port COM virtuel) : passer par un pont RFC 2217 sous Windows (ex. `rfc2217_server.py` des
  exemples de pyserial), puis `$env:CLIO_ARGS="--port rfc2217://host.docker.internal:2217"`.

Port 8080 déjà pris (« port is already allocated ») : `$env:CLIO_PORT="8106"` avant `docker compose up`,
puis https://clio-sentinel.localhost:8106.

**Accès depuis les autres PC du réseau** : `$env:CLIO_HOST="0.0.0.0"` avant `docker compose up`, puis ouvrir le
port dans le pare-feu Windows (PowerShell admin, une seule fois, réseau privé seulement) :
```powershell
New-NetFirewallRule -DisplayName "Clio Sentinel" -Direction Inbound -Protocol TCP -LocalPort 8106 -Action Allow -Profile Private
```
Les autres PC ouvrent alors `https://<IP du serveur>:8106` (IP donnée par `ipconfig`). Le dashboard n'a pas de mot
de passe : tout le réseau peut le voir et changer les réglages et les notes des trajets. Le certificat n'est
fait que pour cette machine : les navigateurs affichent un avertissement à accepter.

**4.** Ouvrir https://clio-sentinel.localhost:8080 et accepter l'avertissement du certificat, ou importer `certs\selfsigned.pem`
dans Windows (*Autorités de certification racines de confiance*) pour ne plus l'avoir. `docker compose logs -f`
pour suivre le script, `docker compose down` pour l'arrêter (la base reste dans le volume).

Les variables `$env:…` ne durent que le temps de la fenêtre PowerShell. Le conteneur, lui, les garde : il
n'est utile de les redonner que pour le recréer (`docker compose up` après une mise à jour du projet).

## Enregistrement permanent

```bash
./service.sh install     # le script tourne en fond et démarre avec la session (status / remove)
./lancer.sh              # ouvre la page (lance le script s'il ne tourne pas déjà)
```

Le service attend l'ELM327 et enregistre dès que le calculateur répond (contact mis). Contact coupé, plus de
réponse pendant 10 s : la session se ferme et le script attend la suivante. Ordi éteint en route : rien n'est
perdu, le trajet est clos au lancement suivant. Pour qu'il démarre avec l'ordi sans ouvrir de session :
`sudo loginctl enable-linger $USER`.

## Utilisation

```bash
python src/dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv  # à la main, contact mis
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
| `--server URL` | envoie la base à clio-sentinel-server (voir « Serveur ») |

L'onglet **Paramètres** du dashboard règle tout ça à chaud :
- affichage (gardé dans le navigateur) : rafraîchissement de la page, durée du graphique ;
- acquisition (appliqué par le script jusqu'à son arrêt) : fréquence max, pause entre requêtes OBD, fréquence du CSV,
  mode rapide (le basculer relance la connexion à l'ELM327).

Au démarrage, le script demande au calculateur quels PID il supporte et n'affiche que ceux-là.
Pour alléger ou compléter les mesures, édite la liste `METRICS` en haut de `src/etat.py`.

## Courbes

Chaque tuile porte une mini-courbe des 2 dernières minutes (durée réglable dans Paramètres), sauf la pression
atmosphérique et la température extérieure, presque constantes. Un clic sur une tuile ouvre sa courbe détaillée :
1 min, 5 min, 15 min ou trajet entier, mise à jour en direct sur le trajet en cours, et une liste pour revoir les
trajets passés enregistrés dans la base. Le curseur donne la valeur et l'heure exactes ; les seuils d'alerte de la
métrique sont tracés quand ils tombent dans l'échelle. La courbe est coupée là où le calculateur n'a pas répondu.
Échap ou un clic à côté pour fermer.

## Trajets

Le principe : **les mesures brutes ne sont jamais modifiées, les trajets sont calculés par-dessus.**

| Niveau | C'est quoi | Détecté par |
|---|---|---|
| session | une connexion au calculateur (contact mis) | le calculateur répond |
| marche moteur | le moteur tourne | régime > 400 tr/min (un régime bas de moins de 3 s ne coupe pas) |
| trajet | marches moteur séparées de moins de 5 min | la voiture a roulé (> 5 km/h) ; sinon « moteur allumé sans rouler » |

Un trajet est clos quand le moteur est arrêté depuis 5 min. Il porte un identifiant unique (UUID), un nom lisible
(`2026-10-06 · trajet 1 · 08:01`, compteur remis à 1 chaque jour), une fiche (durée, distance calculée depuis la
vitesse, démarrage à froid ou à chaud, maximums, arrêts moteur, part sans réponse, VIN) et une note libre saisie
dans l'onglet **Trajets**. Les événements (démarrage, arrêt moteur, passage d'un seuil d'alerte) sont gardés à part.

Les seuils sont en haut de `src/trajets.py`. Après un changement, tout se recalcule depuis le brut, identifiants
et notes gardés :

```bash
python src/trajets.py --rebuild      # liste aussi les trajets
```

## Données

Tout est dans une base SQLite, `data/clio.db`. L'ELM327 interroge les PID l'un après l'autre : chaque valeur
porte l'instant exact où le calculateur a répondu, pas l'heure de la boucle. Une non-réponse est gardée (valeur
vide) : on sait quand un capteur a décroché. La suralimentation calculée prend l'horodatage de la pression
d'admission. Écriture groupée une fois par seconde, environ 3 Mo par heure de route.

| Table | Contenu |
|---|---|
| `sessions` | brut : `id` (UUID), `start_ms`, `end_ms`, `source` (adaptateur), `vin`, `version` (commit git), `metrics` |
| `samples` | brut : `session_id`, `t_ms` (ms depuis 1970, UTC), `key` (`rpm`, `coolant`… comme `METRICS`, plus `boost`), `value` |
| `trips` | calculé : un trajet, sa fiche et sa note (voir plus haut) |
| `events` | calculé : `t_ms`, `kind` (`demarrage`, `arret_moteur`, `alerte`), `key`, `value`, `detail` |

Une base à l'ancien format (un trajet par lancement) est convertie toute seule au premier lancement.
L'heure est celle de l'ordi : il doit être à l'heure. Dans Docker, `CLIO_VERSION=$(git describe --always)`
devant `docker compose up` enregistre la version (l'image n'a pas le dossier `.git`).

```bash
sqlite3 data/clio.db "SELECT datetime(t_ms/1000, 'unixepoch', 'localtime'), key, value
                      FROM samples WHERE key = 'coolant' ORDER BY t_ms DESC LIMIT 10"
```

API : `/api/trips` (trajets avec leur fiche et leurs alertes), `POST /api/trips/note` (`{"id": …, "note": …}`),
`/api/history?key=rpm[&trip=ID][&from=MS&to=MS]` (points `[t_ms, valeur]` du dernier trajet par défaut, moyennés
par tranches au-delà de 2000 points). `/api/state` donne aussi `ts`, l'horodatage de la dernière valeur de chaque
métrique, et `trip`, l'identifiant du trajet en cours.

## Limites

- Avec un ELM327, les PID sont interrogés l'un après l'autre : quelques Hz au total.
- La température d'huile et la pression de rail sont rarement dispo en OBD standard chez Renault
  (mesures constructeur en UDS : voir le repo de scan du calculateur).

## Serveur

[clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) tourne à la maison et garde
l'historique de tous les trajets. On donne son adresse au dashboard ; avec plusieurs adresses (séparées par des
virgules), la première qui répond est prise : la maison en direct sur le Wi‑Fi, sinon le VPN WireGuard sur la route.

```bash
python src/dashboard_clio.py --server http://192.168.1.13:8207,http://10.100.0.1:8207
```

Toutes les 60 s, le dashboard envoie au serveur les mesures qu'il n'a pas encore, par paquets, puis les trajets
s'ils ont changé. Hors réseau, il réessaie sans bruit ; un envoi coupé est refait au suivant. La base locale
n'est jamais modifiée. HTTP simple, sans jeton : le serveur n'est joignable que depuis la maison et le VPN.

| Option | Variable | Rôle |
|---|---|---|
| `--server` | `CLIO_SERVER` | adresse(s) du serveur, séparées par des virgules |
| `--server-name` | `CLIO_SERVER_NAME` | nom de ce PC sur la page du serveur (défaut : nom de la machine) |

- Service : `CLIO_ARGS="--server http://192.168.1.13:8207,http://10.100.0.1:8207" ./service.sh install`.
- Docker : `CLIO_SERVER=...` avant `docker compose up` (`$env:CLIO_SERVER=...` sous Windows). Serveur sur la
  même machine que le conteneur : `http://host.docker.internal:8207`.
- Envoi à la main, sans le dashboard : `python src/synchro.py --server ...`.
