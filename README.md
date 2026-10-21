# Car Sentinel

Dashboard OBD temps réel pour les deux voitures de la maison, sur PC portable ou sur tablette Android, via un
adaptateur ELM327 branché sur la prise OBD :

- **Renault Clio IV dCi 90** (moteur K9K : diesel turbo, rampe commune, EGR) ;
- **Ford Fiesta VII (2022) 1.0 EcoBoost 95 FlexiFuel ST-Line** (3 cylindres essence turbo, injection directe,
  bicarburation essence / E85).

`src/car_sentinel.py` lit le calculateur moteur et sert le dashboard sur http://car-sentinel.localhost:8080
(fonctionne hors ligne), plus une API JSON (`/api/state`, `/api/meta`, `/api/settings`). **La voiture est
reconnue toute seule** au branchement, et le dashboard s'habille pour elle : logo, couleurs (Renault noir et jaune,
Ford bleu nuit), compte-tours, turbo, mesures, étalonnage, éco-conduite, carburant.

Fusion de [clio-sentinel](https://github.com/Aerandir1/clio-sentinel) et de
[fiesta-sentinel](https://github.com/Aerandir1/fiesta-sentinel), dont les historiques sont gardés dans celui-ci.
Un seul adaptateur et un seul service pour les deux voitures : plus besoin d'arrêter l'un pour lancer l'autre.

Autour :
- [clio-hub](https://github.com/Aerandir1/clio-hub) : la page d'accueil 3D ; elle lit `/api/state` sur le port 8080
  (autorisé pour les pages servies depuis 127.0.0.1 / localhost) ;
- [clio-autoradio](https://github.com/Aerandir1/clio-autoradio) : radio, navigation, CarPlay, Spotify ;
- [clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) : le serveur de la maison, qui reçoit
  les deux voitures (une origine et un VIN chacune) et garde tout l'historique (voir « Serveur »).

```
src/
  car_sentinel.py     point d'entrée : options, lancement de l'acquisition et du serveur
  voitures.py         les voitures connues, et la reconnaissance de celle qui est branchée (VIN)
  clio.py, fiesta.py  tout ce qui est propre à chaque voiture : mesures (METRICS), mesures constructeur (FALLBACK),
                      éco-conduite, démo, jauges
  etat.py             état partagé (voiture en cours, valeurs, min / max, réglages), CSV
  acquisition.py      lecture de l'ECU par l'ELM327
  requetes.py         lecture rapide : priorités, requêtes groupées et adressées au calculateur moteur
  scan_pid.py         scan des PID : la façon la plus rapide d'interroger la voiture
  constructeur.py     mesures constructeur (Ford, Renault : UDS, lecture seule) quand l'OBD standard manque
  etalonnage.py       gain et décalage des mesures constructeur (onglet Étalonnage)
  diagnostic.py       onglet Diagnostic : codes défaut et scan UDS / KWP, lecture seule (liste blanche)
  serveur.py          serveur HTTP : page, logos, API JSON
  config.py           lecture de car.conf
  reseau.py           adresses de la page (ce PC et réseau)
  demo.py             mode démo : trajet simulé de la voiture en cours
  donnees.py          base SQLite : sessions, mesures horodatées, trajets, événements
  trajets.py          découpage des mesures en trajets, fiche de chaque trajet, éco-conduite
  synchro.py          envoi des bases vers clio-sentinel-server
web/dashboard.html    le dashboard
web/logo/             ovales Ford, losange et texte Renault
android/              l'appli Android (tablette autonome, ELM327 en USB)
desktop/              l'appli Windows (Electron) : service en fond, icône d'état dans la zone de notification
data/                 bases, étalonnages, réglages (pas dans git)
car.conf.exemple      modèle des réglages : copié en car.conf (pas dans git) au premier lancement
lancer.sh             Linux : lancement en local (venv + navigateur)
service.sh            Linux : enregistrement permanent, service systemd lancé avec la session
lancer.bat, lancer.ps1   Windows : lancement en local (Python + venv + navigateur), double-clic sur lancer.bat
service.ps1           Windows : enregistrement permanent, lancé à l'ouverture de session
windows-commun.ps1    Windows : fonctions communes aux deux scripts
```

Le script tourne directement sous **Linux** et sous **Windows** (Python 3.9 ou plus), sans Docker, et sur
tablette **Android** (appli dans `android/`).

## Quelle voiture ?

À chaque connexion au calculateur, le script lit le **VIN** (mode 09) : ses trois premiers caractères donnent le
constructeur, `VF1` pour Renault (la Clio), `WF0` pour Ford (la Fiesta). Sans VIN, le **type de carburant**
annoncé par le calculateur (01 51) départage : diesel pour la Clio, essence / éthanol pour la Fiesta. Le journal
le dit (« Voiture : Renault Clio IV dCi 90 (VIN VF1…) ») et le dashboard change d'habit dans la seconde.

Sans voiture branchée (ordi à la maison, démo), le dashboard garde **la dernière voiture vue**. Elle se change dans
**Réglages → Voiture** (la démo simule alors l'autre) ; une voiture branchée, elle, est imposée par son VIN.
`--voiture clio` (ou `fiesta`) choisit celle du lancement.

Chaque voiture garde ses propres fichiers dans `data/` :

| Fichier | Contenu |
|---|---|
| `clio.db`, `fiesta.db` | mesures, trajets, événements de chaque voiture |
| `clio-demo.db`, `fiesta-demo.db` | la démo de chaque voiture, jamais mêlée aux vrais trajets ni envoyée au serveur |
| `etalonnage-clio.json`, `etalonnage-fiesta.json` | étalonnage des mesures Renault / Ford |
| `scan-pid-clio.json`, `scan-pid-fiesta.json` | réglages de lecture trouvés par le scan des PID |
| `reglages.json` | prix du carburant de chaque voiture (gazole, E85), dernière voiture vue, serveur |
| `diag/` | scans et journaux de l'onglet Diagnostic |

### Venir de clio-sentinel et fiesta-sentinel

Arrêter les deux anciens services (`systemctl --user disable --now clio-sentinel fiesta-sentinel`, ou
`service.ps1 remove` sous Windows), puis copier leurs fichiers dans `data/` :

```bash
cp ../Clio/clio-sentinel/data/clio.db*          data/   # avec ses -wal et -shm
cp ../Fiesta/fiesta-sentinel/data/fiesta.db*    data/
cp ../Clio/clio-sentinel/data/etalonnage.json   data/etalonnage-clio.json
cp ../Fiesta/fiesta-sentinel/data/etalonnage.json data/etalonnage-fiesta.json
cp -r ../Clio/clio-sentinel/data/diag           data/
cp ../Fiesta/fiesta-sentinel/data/scan-pid.json data/scan-pid-fiesta.json   # s'il existe
```

Les bases sont mises au format actuel au premier lancement (trajets recalculés, avec l'éco-conduite pour la
Clio aussi ; identifiants et notes gardés). Le serveur reconnaît chaque base à sa première session : il continue
chaque voiture là où il en était. Le prix du carburant se règle une fois par voiture dans Réglages → Carburant
(un ancien `reglages.json` copié dans `data/` donne son prix à la dernière voiture vue, la Fiesta par défaut).
Avant la fusion, chaque dashboard enregistrait la voiture branchée quelle qu'elle soit : `clio.db` peut contenir
des sessions de la Fiesta (VIN `WF0…`) et inversement ; elles y restent, reconnaissables à leur VIN.

## Installation et lancement sous Linux

```bash
sudo usermod -aG uucp $USER          # accès au port de l'ELM327 (dialout sous Debian / Ubuntu), puis déconnexion/reconnexion
./lancer.sh                          # crée ~/.venvs/obd au premier lancement, lance le script et ouvre la page
```

À la main :
```bash
python -m venv ~/.venvs/obd && source ~/.venvs/obd/bin/activate
pip install -r requirements.txt
python src/car_sentinel.py
```

Avec `ADAPTATEUR` vide, le script cherche dans `ttyUSB*`, `ttyACM*` et `rfcomm*` celui qui répond à `ATI`
(« Adaptateur trouvé : ELM327 v1.5 sur /dev/ttyACM0 ») et attend la clé si elle n'est pas branchée. S'il n'a pas
le droit d'ouvrir le port, le journal donne la commande `usermod` à taper.

## Installation et lancement sous Windows

Double-cliquer sur **`lancer.bat`** (ou, dans PowerShell, `.\lancer.bat`). Au premier lancement, il installe
Python si besoin (winget, pour l'utilisateur seulement), crée l'environnement `%USERPROFILE%\.venvs\obd`, y
installe les dépendances, puis lance le script et ouvre la page. La fenêtre reste ouverte tant que le script
tourne (la fermer, ou Ctrl+C, l'arrête). Des options peuvent suivre : `.\lancer.bat --port COM3 --log trajet.csv`.

L'adaptateur, dans `car.conf` :
- **USB** : laisser `ADAPTATEUR` vide. Le script essaie les ports COM, clés USB d'abord (CH340, FTDI, CP210x,
  Prolific), et garde celui qui répond à `ATI` (« Adaptateur trouvé : ELM327 v1.5 sur COM3 »). Si Windows ne
  reconnaît pas la clé (pas de port COM dans le Gestionnaire de périphériques), installer le pilote de sa puce
  (souvent CH340). `ADAPTATEUR=COM3` force un port.
- **Bluetooth** : appairer l'adaptateur dans Windows, qui lui donne un port COM ; il est trouvé comme une clé USB
  (plus lentement), ou donné par `ADAPTATEUR=COM5`.
- **Wi-Fi** (PC connecté au Wi-Fi de l'adaptateur) : `ADAPTATEUR=socket://192.168.0.10:35000` et `DEBIT=38400`.

Un port COM ne s'ouvre que par un seul programme : fermer les autres logiciels OBD avant (le témoin indique
« port occupé » sinon).

Port 8080 déjà pris : `PORT_PAGE=8106` dans `car.conf`, relancer, puis http://car-sentinel.localhost:8106.

**Accès depuis les autres PC du réseau** : `RESEAU=oui` dans `car.conf`, relancer, puis ouvrir le port dans le
pare-feu Windows (PowerShell admin, une seule fois, réseau privé seulement) :
```powershell
New-NetFirewallRule -DisplayName "Car Sentinel" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
```

## Réglages : car.conf

Tous les réglages sont dans **`car.conf`**, à la racine du projet, lu par le script à chaque lancement. Il
est créé au premier lancement depuis `car.conf.exemple`, qui explique chaque ligne :

| Réglage | Rôle |
|---|---|
| `RESEAU` | `oui` : page accessible depuis les autres appareils du réseau (voir « Accès depuis le réseau ») |
| `PORT_PAGE` | port de la page (défaut 8080) |
| `ADAPTATEUR`, `DEBIT` | port de l'ELM327 (vide : auto-détection) : `/dev/ttyACM0`, `COM3`, `socket://192.168.0.10:35000`… |
| `SERVEUR`, `NOM_PC` | envoi vers clio-sentinel-server (voir « Serveur ») ; l'onglet Réglages passe devant une fois modifié |
| `AUTRES_OPTIONS` | autres options du script (voir « Utilisation »), ex. `--fast --log logs/trajet.csv` |

Après un changement : relancer le script (`./service.sh restart` ou `service.ps1 restart` pour le service).
Une option donnée en ligne de commande passe avant `car.conf`.

## Adresse de la page

Sur ce PC, la page est servie en **http://car-sentinel.localhost:8080** (HTTP simple, pas de certificat).
Chrome, Edge et Firefox envoient tout nom en `.localhost` vers l'ordi lui-même : rien à mettre dans le fichier
hosts. http://127.0.0.1:8080 et http://localhost:8080 redirigent vers ce nom (l'API, elle, répond aussi sur
127.0.0.1, pour le hub).

## Enregistrement permanent

Linux (service systemd de l'utilisateur) :
```bash
./service.sh install     # le script tourne en fond et démarre avec l'ordi (une seule fois)
./service.sh restart     # après un changement dans car.conf
./service.sh status      # tourne-t-il ? adresses de la page, ou l'erreur qui l'empêche de démarrer
./service.sh logs        # journal en direct
./service.sh stop        # arrêt (start pour relancer, remove pour supprimer le service)
./lancer.sh              # ouvre la page (relance le service s'il ne tourne pas)
```
Pour qu'il démarre avec l'ordi sans ouvrir de session : `sudo loginctl enable-linger $USER`.

Windows (raccourci dans le dossier Démarrage : sans fenêtre, à chaque ouverture de session, sans droits admin) :
```powershell
powershell -ExecutionPolicy Bypass -File .\service.ps1 install    # une seule fois
powershell -ExecutionPolicy Bypass -File .\service.ps1 restart    # après un changement dans car.conf
powershell -ExecutionPolicy Bypass -File .\service.ps1 status     # tourne-t-il ? adresses, ou l'erreur
powershell -ExecutionPolicy Bypass -File .\service.ps1 logs       # journal en direct (logs\car.log)
powershell -ExecutionPolicy Bypass -File .\service.ps1 stop       # arrêt (start pour relancer, remove pour supprimer)
.\lancer.bat                                                      # ouvre la page (relance le script s'il ne tourne pas)
```

Le script attend l'ELM327 et enregistre dès que le calculateur répond (contact mis), dans la base de la voiture
reconnue. Contact coupé, plus de réponse pendant 10 s : la session se ferme et le script attend la suivante, qui
peut être l'autre voiture. Ordi éteint en route : rien n'est perdu, le trajet est clos au lancement suivant.

## Accès depuis le réseau (Raspberry Pi dans la voiture)

Par défaut la page n'est servie qu'à la machine elle-même. Avec **`RESEAU=oui`** dans `car.conf` (puis
`./service.sh restart`), elle l'est aussi aux autres appareils du réseau (téléphone, tablette sur le tableau de
bord, PC). À la main : `python src/car_sentinel.py --lan`.

Au lancement, le script affiche les adresses à ouvrir (`./service.sh status` les redonne) :
`http://<nom>.local:8080` (le nom de la machine, résolu par mDNS : avahi, installé d'office sur Raspberry Pi OS)
et `http://<IP>:8080` pour chaque adresse réseau.

- **Ça ne s'ouvre pas** : `./service.sh status` doit montrer les lignes « sur le réseau » ; les deux appareils
  doivent être sur le même Wi‑Fi (pas un réseau invité, qui isole les appareils) ; un pare-feu sur la machine
  (ufw, firewalld) doit laisser passer le port 8080.
- **Dans la voiture**, sans box : le Pi crée son propre Wi‑Fi, sur lequel se connectent téléphone et tablette.
  Avec NetworkManager (Raspberry Pi OS Bookworm) :
  ```bash
  sudo nmcli device wifi hotspot ifname wlan0 ssid Voiture password "un-mot-de-passe"
  sudo nmcli connection modify Hotspot connection.autoconnect yes      # au démarrage du Pi
  ```
  Le Pi y a toujours l'adresse `10.42.0.1` : la page est sur http://10.42.0.1:8080 (adresse fixe, à mettre en
  favori ; tous les téléphones ne résolvent pas les noms `.local`). Pour le lancer au démarrage du Pi sans ouvrir
  de session : `RESEAU=oui` dans `car.conf`, puis
  ```bash
  ./service.sh install
  sudo loginctl enable-linger $USER
  ```
- **Sécurité** : pas de mot de passe sur le dashboard ; tout appareil du réseau peut le voir et changer les
  réglages d'acquisition et les notes des trajets, et lancer un diagnostic (en lecture seule). À réserver au
  Wi‑Fi de la voiture ou de la maison, protégé par mot de passe, jamais un Wi‑Fi public.
- **Hub** : une page servie par la même machine sur un autre port (clio-hub) et ouverte par le même nom
  (`http://10.42.0.1:8000`) peut lire `/api/state`.

## Appli Windows (zone de notification)

`desktop/` emballe le dashboard en une appli Windows 11 installable, **Car Sentinel**, sans rien d'autre à
installer : elle embarque son Python (3.14, distribution « embeddable » de python.org, avec `obd` et `pyserial`),
le code de `src/` et le dashboard de `web/`. Elle remplace `lancer.bat` et `service.ps1` :

- elle fait tourner le dashboard en fond, sans fenêtre, et l'affiche dans sa propre fenêtre (clic sur l'icône,
  ou « Ouvrir dans le navigateur » dans le menu). Fermer la fenêtre la cache : l'enregistrement continue ;
- elle se lance à l'ouverture de session (icône seule), ce qui se décoche dans le menu de l'icône ;
- l'**icône** (le compte-tours de l'appli Android) montre l'état d'un coup d'œil. L'arc donne la liaison avec la
  voiture : gris = pas d'ELM327, orange = ELM327 branché mais calculateur muet (contact coupé), vert = le
  calculateur répond, bleu = démo, rouge = le dashboard ne répond pas (relancé tout seul). La pastille en bas
  à droite donne le serveur : verte = connecté, rouge = injoignable, grise = connexion, aucune = pas de serveur.
  Le survol donne le détail (adaptateur et port, protocole, heure de la dernière synchro) ;
- une notification quand une voiture est reconnue (« Fiesta 1.0 EcoBoost branchée ») et quand son calculateur
  se tait depuis 20 s ;
- réglages, bases et journal sont dans `%APPDATA%\Car Sentinel` (menu : « Réglages (car.conf) », « Journal »,
  « Dossier des données », puis « Redémarrer le service » après un changement de `car.conf`). Pour reprendre les
  bases du dashboard lancé par `service.ps1` : `service.ps1 remove`, puis copier son `data\` dans
  `%APPDATA%\Car Sentinel\data` avant le premier lancement de l'appli ;
- si un dashboard tourne déjà sur le port (`service.ps1`), l'appli s'en sert au lieu d'en lancer un second.

Compilation (Node.js, Python 3 pour préparer le Python embarqué ; depuis Linux, **Wine** pour l'installeur) :

```bash
cd desktop
npm install
npm run dist      # dist/Car Sentinel Setup 1.0.0.exe (installeur, pour l'utilisateur seulement)
npx electron-builder --win zip --x64   # sans Wine : dist/Car Sentinel-1.0.0-win.zip, à dézipper et lancer
npm start         # essai sur ce PC, avec le Python du PC (CAR_PYTHON=~/.venvs/obd/bin/python)
```

Les icônes de `desktop/icones/` se redessinent par `npm run icones` (rsvg-convert et ImageMagick).

### Mises à jour de l'appli

L'appli se met à jour depuis clio-sentinel-server (route `/maj/`), à l'adresse qui répond à la synchro (Wi-Fi de
la maison ou VPN) : elle cherche une nouvelle version dès que le serveur répond, puis toutes les 6 h, la
télécharge en fond, propose de l'installer par une bulle, sinon l'installe à sa fermeture. Le menu de l'icône
donne la version installée et « Rechercher une mise à jour ». Sans serveur réglé (`SERVEUR`), pas de mise à jour.

Publier une version (une fois : le même jeton dans `MAJ_JETON` du serveur et dans `desktop/.maj-jeton`) :

```bash
cd desktop
npm version patch --no-git-tag-version   # 1.0.0 → 1.0.1 dans package.json (minor, major : plus grand saut)
npm run dist          # dist/car-sentinel-setup-1.0.1.exe, .blockmap et latest.yml (Wine sous Linux, ou Windows)
npm run publier       # envoyés au serveur (SERVEUR de car.conf, ou -- --server 10.100.0.1)
```

Seul l'installeur NSIS se met à jour (pas le zip). Une version déjà sur le serveur, ou plus ancienne, est refusée.

## Tablette Android (APK)

`android/` fait de la tablette un dashboard autonome, sans PC : l'appli **Car Sentinel** embarque le code Python
de `src/` (Chaquopy, Python 3.14) et le dashboard de `web/`. La clé ELM327 se branche en **USB sur la tablette**
(câble OTG USB-C) : Android ne donne pas de `/dev/ttyACM0` aux applis, le port passe par l'API USB host
(usb-serial-for-android) sous l'adresse `androidusb://` (`android/app/src/main/python/car_usb`). La même tablette
passe d'une voiture à l'autre : elle reconnaît celle où on la branche.

- un service au premier plan lit l'ELM327 et sert la page sur le port 8080, même écran éteint ;
  l'appli l'affiche en plein écran (écran toujours allumé). « Quitter » dans la notification arrête tout ;
- au premier lancement, l'appli demande à sortir de l'économie de batterie (sinon Samsung l'endort et
  l'envoi au serveur s'arrête en arrière-plan) ; refusé, ça se règle dans Paramètres → Applications →
  Car Sentinel → Batterie → Non restreinte ;
- à l'arrivée de la clé, Android propose d'ouvrir Car Sentinel : cocher « toujours » donne l'accès au port
  une fois pour toutes. Puces reconnues : USB natif (CDC ACM), FTDI, CH340, CP210x, PL2303 ;
- bases, réglages et étalonnages sont dans le stockage de l'appli (`files/data/`), à part de ceux du PC ;
- synchro vers clio-sentinel-server : l'APK part avec le `SERVEUR` du `car.conf` du PC qui l'a compilé, et la
  tablette se déclare sous son modèle (ex. « samsung SM-X200 ») ; les deux se changent dans l'onglet Réglages.
  Sur la route, le serveur n'est joignable que par le VPN (WireGuard sur la tablette) ;
- le bouton retour ferme la courbe ouverte, puis revient au tableau de bord ; le thème « Auto » suit le mode
  sombre d'Android ;
- GPS (autorisation demandée au premier lancement) : une position par seconde enregistrée avec les mesures
  (`gps_lat`, `gps_lon`, `gps_speed`, `gps_alt`, `gps_head`, `gps_acc`), carte du trajet sur le serveur, et
  points de passage (voir « Tronçons »). Témoin « GPS » en bas de l'écran central du mode Conduite.

Car Sentinel est une autre appli que l'ancienne Fiesta Sentinel (autre identifiant, `fr.car.sentinel`) : les
deux s'installent côte à côte, mais une seule doit garder la clé USB. Désinstaller Fiesta Sentinel une fois Car
Sentinel en place (ses données restent dans l'ancienne appli jusque-là ; le serveur, lui, a tout).

Compilation (JDK 17, SDK Android dans `~/Android/Sdk`, chemin dans `android/local.properties`) :

```bash
cd android
JAVA_HOME=/usr/lib/jvm/java-17-openjdk ./gradlew assembleRelease
# APK : android/app/build/outputs/apk/release/app-release.apk (signé avec la clé de debug, usage perso)
adb install -r app/build/outputs/apk/release/app-release.apk   # ou copier l'APK sur la tablette
```

Sur la tablette sans `adb` : copier l'APK, l'ouvrir, autoriser « installer des applis inconnues ». Journal :
`adb logcat -s python.stdout python.stderr CarSentinel`.

## Utilisation

```bash
python src/car_sentinel.py --port /dev/ttyUSB0 --log trajet.csv  # à la main, contact mis (Windows : --port COM3)
```

Les options ci-dessous passent avant `car.conf` ; pour les garder, les mettre dans `AUTRES_OPTIONS`.

| Option | Rôle |
|---|---|
| `--port` | port série de l'ELM327 (auto-détection si absent) |
| `--voiture clio` / `fiesta` | voiture au lancement, tant que le VIN n'a pas parlé (défaut : la dernière vue) |
| `--data DOSSIER` | dossier des bases, étalonnages et réglages (défaut `data/`) |
| `--no-db` | n'enregistre rien |
| `--log FICHIER.csv` | enregistre les mesures en CSV |
| `--log-hz` | fréquence du CSV (défaut 5 Hz) |
| `--delay` | pause entre deux requêtes OBD (si l'ELM327 décroche) |
| `--http` | port de la page web (défaut 8080) |
| `--bind` | adresse d'écoute (défaut 127.0.0.1) |
| `--lan` | page accessible depuis les autres appareils du réseau (voir « Accès depuis le réseau ») |
| `--demo` | démarre en mode démo (trajet simulé de la voiture en cours, base à part) |
| `--fast` | mode rapide python-obd à la connexion (la lecture, elle, se règle seule : voir « Vitesse de lecture ») |
| `--max-hz` | fréquence max d'acquisition (défaut 20) |
| `--server URL` | envoie les bases à clio-sentinel-server (voir « Serveur ») |

Le dashboard a ses onglets dans la barre du bas (à portée de pouce sur la tablette) :
- **Conduite** : un combiné d'instruments. Compte-tours et turbo en cadrans, écran central avec le rapport engagé
  (lu par le PCM de la Fiesta, déduit du régime et de la vitesse sur la Clio), une flèche quand il faut monter le
  rapport, la vitesse, la conso, le coût du trajet, l'heure et la température extérieure, des témoins (eau froide
  ou chaude, huile, batterie, calculateur, autres seuils) ; et des jauges à segments pour les mesures cochées
  « Mode conduite » dans Réglages → Disposition des tuiles ;
- **Détail** : les jauges et toutes les tuiles rangées par catégorie, avec mini-courbes et min / max ; un bandeau
  rouge apparaît en haut dès qu'une mesure dépasse son seuil ;
- **Trajets**, **Diagnostic**, **Étalonnage** (en trois étapes) et **Réglages** (cartes repliables, et
  sous-onglet Disposition).

Au premier lancement, un écran tactile (la tablette) ouvre sur Conduite et un PC sur Détail ; ensuite, le dernier des
deux choisi. Thème **Nuit** ou **Jour** (clair, lisible au soleil), ou **Auto** : celui du système (sur la
tablette, le mode sombre d'Android).

L'onglet **Réglages** règle tout ça à chaud :
- voiture affichée sans voiture branchée (voir « Quelle voiture ? ») ;
- prix du carburant de la voiture affichée (voir « Coût des trajets ») ;
- affichage (gardé dans le navigateur) : thème, rafraîchissement de la page, durée des mini-courbes ;
- acquisition (appliqué par le script jusqu'à son arrêt) : fréquence max, pause entre requêtes OBD, fréquence du CSV,
  mode rapide (le basculer relance la connexion à l'ELM327) ;
- **mode démo** : un trajet simulé en boucle de la voiture affichée (démarrage, ville, voie rapide, arrêt
  moteur), pour voir le dashboard sans voiture. Tout se passe comme avec la voiture (session, mesures, trajets),
  mais dans une base à part, `data/<voiture>-demo.db` (VIN `VF1DEMO…` ou `WF0DEMO…`), jamais envoyée au serveur.
  Changer de voiture dans Réglages relance la démo avec l'autre. Arrêter la démo reprend l'ELM327 ; supprimer
  `data/<voiture>-demo.db` efface la démo locale.

Au démarrage, le script demande au calculateur quels PID il supporte et n'affiche que ceux-là. Pour une mesure
absente en OBD standard, il essaie l'équivalent constructeur (`FALLBACK` de `src/clio.py` ou `src/fiesta.py`) :
des lectures UDS (service 0x22 sur 7E0) qui n'écrivent jamais rien dans le calculateur ; chacune n'est gardée que
si le calculateur répond (journal : « Mesures Renault lues en plus de l'OBD standard », « Mesures Ford… »).
Identifiants tirés d'OBDb ([Renault-Clio](https://github.com/OBDb/Renault-Clio),
[Ford-Fiesta](https://github.com/OBDb/Ford-Fiesta), CC BY-SA 4.0) et du scan UDS de clio-iv-obd-utils.

- **Clio** : en standard, régime, vitesse, températures eau / admission / extérieure, pression d'admission et atmo,
  charge, pédale, volet d'admission, débit d'air, pression rail, conso, EGR et son écart, tension. En Renault :
  température d'huile (`22 2007`), tension (`22 2005`), pédale (`22 202E`), d'office ; pression atmo (`22 2009`),
  consigne EGR (`22 2408`) et débit carburant (`22 2806`) à étalonner.
- **Fiesta** : d'après le relevé OBDb d'une Fiesta 2022, le PCM annonce en standard régime, vitesse, températures
  eau / admission / huile / extérieure / catalyseur, pression d'admission et atmo, charge, papillon, pédale,
  conso, niveau de carburant, richesse, corrections court / long terme, avance et tension. En Ford : voir le
  tableau de « Vitesse de lecture ». Pas interrogés : le débit d'air (01 10), qui ne répond jamais sur la voiture,
  et la température d'air de suralimentation (Ford `0461`), toujours à -40 °C.

Pour alléger ou compléter les mesures d'une voiture : sa liste `METRICS` et son `FALLBACK`, dans son module.
Une autre voiture s'ajoute avec un module de plus sur le même modèle, déclaré dans `src/voitures.py`.

### Vitesse de lecture

Un ELM327 ne pose qu'une question à la fois : la fréquence dépend du nombre de requêtes par tour et de leur
durée. Une requête par mesure (≈ 45 ms sur la Fiesta), toutes à chaque tour, c'est environ 28 requêtes, 1,3 s par
tour, 0,8 Hz. `src/requetes.py` réduit les deux, sur les deux voitures :

- **priorités** : régime, vitesse, pédale, papillon, pression d'admission, charge, conso et avance sont lus à
  chaque tour ; richesse, corrections court terme, débit d'air, pression rail, EGR toutes les 0,5 s ;
  températures, tension et correction long terme toutes les 2 s ; pression atmo, température extérieure, éthanol
  toutes les 5 s, niveau de carburant toutes les 10 s (tableau `PERIOD`). Les mesures constructeur gardent la
  période de leur `FALLBACK` ;
- **groupage** : jusqu'à 6 PID dans une requête OBD (`01 0C 0D 0B 04 49 11`), et 4 mesures constructeur dans une
  requête UDS (`22 033E 0462 …`) si le calculateur l'accepte ;
- **requêtes courtes** : adressées au seul calculateur moteur (`ATSH 7E0`) avec le nombre de réponses attendu
  (`010C0D1`) : l'ELM327 rend la main dès la réponse, sans attendre d'éventuels autres calculateurs.

Chaque levier est essayé à la connexion et abandonné si l'adaptateur ou le calculateur ne suit pas. Si le
calculateur ne rend qu'une partie d'un groupe (sur la Fiesta, une réponse limitée à une trame : pédale, papillon et
charge manquaient deux fois sur trois dans un groupe de 6), le groupe est relu mesure par mesure et la taille des
groupes ramenée à ce qu'il rend (journal : « le calculateur ne rend que 3 PID OBD par requête »). Le journal dit
ce qui est retenu (« Lecture : requêtes au seul calculateur moteur (7E0), réponse sans attente… »). Le témoin
« Calculateur » affiche la fréquence des tours et le temps de réponse moyen d'une requête (ex. `20 Hz · 16 ms`).
Le tour est bridé par la fréquence max (défaut 20 Hz, onglet Réglages) : plus de mesures veut aussi dire une base
plus grosse.

Mesures Ford de la Fiesta :

| Mesure | DID | Affichée |
|---|---|---|
| huile, tension, pédale, niveau carburant | `1310`, `1172`, `09D4`, `F42F` | d'office (seulement si l'OBD standard ne les donne pas) |
| pression turbo (avant papillon) | `033E` | après validation (onglet Étalonnage) |
| température de culasse | `0334` | après validation |
| wastegate, consigne rail | `0462`, `03DC` | après validation |
| retrait cliquetis cyl. 1, adaptation octane | `03EC`, `03E8` | après validation |
| rapport engagé, vie de l'huile | `1E12`, `054B` | après validation |

#### Scan des PID

`src/scan_pid.py` cherche la façon la plus rapide d'interroger la voiture branchée (reconnue au VIN, ou
`--voiture`). Il répète chaque essai (20 fois par défaut) et ne retient que ce qui répond à tous les coups :

1. aller-retour avec l'adaptateur seul (`ATRV`) : le temps minimal d'une requête ;
2. adressage : diffusion (`7DF`), calculateur moteur seul (`7E0`), avec ou sans nombre de réponses attendu ;
3. délai d'attente de l'ELM327 après la dernière réponse (`ATAT`, `ATST`, 200 ms d'office), qui pèse sur les
   réponses en plusieurs trames et sur les mesures muettes ;
4. chaque mesure à part : temps de réponse, part de réponses, mesures muettes, instables ou lentes ;
5. groupage : 2 à 6 PID OBD, 2 à 4 mesures constructeur par requête, jusqu'à ce que le calculateur ne rende plus
   tout ;
6. la boucle du dashboard pendant 15 s, sans puis avec les réglages trouvés : tours par seconde.

```bash
./service.sh stop                          # le dashboard tient l'adaptateur pendant qu'il tourne
~/.venvs/obd/bin/python src/scan_pid.py    # contact mis, moteur tournant ; --tours 50 pour plus d'essais
./service.sh start
```

Le résultat va dans `data/scan-pid-<voiture>.json` (délai d'attente, taille des groupes, temps de chaque mesure) ;
le dashboard le reprend à la connexion suivante si le VIN est le même (journal : « Lecture : délai d'attente du
scan… »). Supprimer le fichier revient aux réglages d'office.

## Étalonnage

Les mesures constructeur non vérifiées sur la voiture sont lues et leur **valeur brute** est enregistrée (clé
`brut.<mesure>` dans la base), mais leur tuile n'apparaît qu'une fois étalonnées et validées dans l'onglet
**Étalonnage** (celui de la voiture en cours) :

1. choisir la mesure ; la valeur brute et la valeur étalonnée s'affichent en direct ;
2. noter des références : « Noter l'instant » (en roulant, par le passager) ou clic / glisser sur la courbe d'un
   trajet enregistré, puis la valeur lue sur l'ordinateur de bord (la conso en L/100 km ou en L/h). Pour la
   pression turbo de la Fiesta, contact mis moteur arrêté, elle doit valoir la pression atmo (≈ 100 kPa) ; la
   culasse suit la température d'eau ; le rapport doit coller à celui engagé ;
3. « Calculer depuis les références » : une référence fixe le gain, deux ou plus (à des régimes différents) le
   gain et le décalage ; les curseurs affinent, la courbe et l'écart de chaque référence suivent ;
4. « Enregistrer », puis « Valider : afficher la tuile ».

Le réglage est rangé dans `data/etalonnage-<voiture>.json`. Comme la valeur brute est gardée, un étalonnage
corrigé s'applique aussi aux courbes des trajets passés. Les mesures déjà vérifiées peuvent y être réajustées de
la même façon.

**Sauvegardé sur le serveur** : avec la synchro (voir « Serveur »), l'étalonnage de chaque voiture part aussi sur
clio-sentinel-server, et en revient. Un nouveau client (autre PC, tablette réinstallée, `data/` perdu) récupère
tout l'étalonnage dès qu'il joint le serveur, et un étalonnage fait sur la tablette arrive sur le PC (et
inversement) au tour de synchro suivant, toutes les 60 s. Fusion mesure par mesure : la plus récemment réglée
gagne (journal : « Étalonnage … : 3 mesures reprises du serveur »). Le serveur garde chaque version reçue.

## Diagnostic

L'onglet **Diagnostic** lit les codes défaut et fouille les identifiants des calculateurs, sans jamais rien
écrire ni effacer. Pendant un diagnostic, le dashboard laisse l'ELM327 (témoins « diagnostic ») et
l'enregistrement est en pause ; il reprend tout seul après. Pas en mode démo.

**Véhicule** : celui de la voiture en cours par défaut, ou choisi en haut de l'onglet (gardé par le navigateur).
Requêtes, calculateurs et session suivent ce choix :

- **Ford Fiesta VII 1.0 EcoBoost** : prise OBD en CAN, requêtes UDS, réponse à l'adresse + 8 (726 → 72E). Codes
  défaut lus sur tous les calculateurs du réseau Ford (adresses FORScan des Fiesta VII) : moteur (PCM, 7E0), ABS /
  ESP (760), airbags (RCM, 737), direction assistée (PSCM, 730), boîtier carrosserie (BCM, 726), combiné (IPC, 720),
  passerelle (GWM, 716), SYNC (APIM, 7D0), audio (ACM, 727), climatisation (HVAC, 733), aide au stationnement (PAM,
  736), caméra avant (IPMA, 706). Ceux que la voiture n'a pas sont signalés « pas de réponse ». Scan du moteur par
  familles d'identifiants Ford (03xx à 05xx, 09xx, 11xx à 13xx, 1Exx, F4xx), des autres en identification (F1xx)
  et données (DDxx) ; les DID déjà connus du dashboard sont nommés et décodés dans les scans.
- **Renault Clio IV dCi 90 (K9K)** : prise OBD en CAN, requêtes UDS ; moteur (7E0) et tableau de bord (743).
- **Renault Clio II phase 2 (2002) essence** : prise OBD en K-line, sans CAN. Codes défaut OBD 03 / 07, puis
  KWP 18 02 sur l'injection (adresses 7A Renault et 10 OBD, init 5 bauds ou rapide comme en OBD) ; codes
  Renault laissés en hexa. Scan des identifiants locaux (KWP 21, 01 à FF), session de diagnostic Renault 10 C0,
  retour en 10 81. Les autres calculateurs (ABS, airbag, UCH) ne sont pas proposés ; « Autre adresse » permet
  d'en viser un. Un calculateur seul (sur table) qui ne répond pas en OBD est quand même interrogé en KWP ;
  chaque diagnostic laisse le journal de ses échanges dans `data/diag/*.log`.
  Si le protocole détecté ne correspond pas au véhicule choisi, le diagnostic s'arrête avant toute requête
  constructeur.

- **Codes défaut** : OBD 03 (mémorisés), 07 (en attente) et 0A (permanents, effaçables seulement par le
  calculateur lui-même) à tous les calculateurs, puis UDS 19 02 sur chaque calculateur du véhicule. Contact mis
  suffit. En UDS, seuls les codes en défaut sont demandés (masque d'état `AD`) : avec `FF`, un PCM Ford renvoie
  aussi des centaines de codes dont le test n'a simplement pas encore tourné, qui ne sont pas des défauts (un
  calculateur qui ignore le masque les renvoie quand même : ils sont écartés et comptés). Pour chaque code :
  - l'**état** résumé (présent, confirmé, en attente, historique), le **voyant** moteur demandé, et au survol le
    détail des bits d'état (ISO 14229) ;
  - le **type de défaillance** (3e octet des codes UDS, ISO 15031-6) en clair : court-circuit à la masse, circuit
    ouvert, signal invalide, plausibilité… (`P0171-29` : mélange pauvre, signal invalide) ;
  - la **description** des codes génériques SAE (P0…, P2…, U0…, table de python-obd, en anglais) ; les codes propres
    au constructeur (P1…, B1…) sont signalés comme tels.
- **Scan des identifiants** : lecture (UDS 22) de chaque DID d'une plage, sur un calculateur du véhicule ou une
  autre adresse. Les DID qui répondent sont rangés dans `data/diag/*.json`, au format de `uds_scan.py`
  (clio-iv-obd-utils). Cocher deux scans ou plus et « Comparer » liste ce qui change : faire un scan contact mis,
  un au ralenti, un à 2500 tr/min isole les mesures vivantes.
- **Session étendue (10 03)** : certains DID ne répondent qu'en session de diagnostic étendue, comme avec l'outil
  du garage. Moteur coupé (contact mis) et voiture à l'arrêt obligatoires : régime et vitesse sont lus juste avant,
  la session est refusée sinon. Retour en session normale (10 01) à la fin, même si le scan est arrêté ; sans
  message pendant ~5 s, le calculateur y revient de lui-même.

**Gardés et envoyés au serveur** : chaque diagnostic lit d'abord le VIN (OBD 09 02 ; à défaut, celui de la
dernière connexion si c'est la même voiture). Les scans sont rangés dans `data/diag/scan-*.json`, les codes défaut
dans `data/diag/dtc-*.json` (même sans code : « aucun défaut ce jour-là » compte aussi), avec ce VIN. Avec la
synchro, chacun part une fois sur clio-sentinel-server (`data/diag/envoyes.txt` retient ceux déjà reçus), qui les
range par VIN sur sa page **Diagnostics**. Les anciens scans, sans VIN, y sont rattachés au VIN de la voiture quand
le serveur n'en connaît qu'un.

**Liste blanche** (`ALLOWED` dans `src/diagnostic.py`, vérifiée au seul endroit qui écrit sur le port) : seules
partent vers la voiture les lectures (OBD 01, 03, 07, 09, 0A ; UDS 22, 19 02), l'entrée / sortie de session (10 03,
10 01) et son maintien (3E) ; en K-line, les équivalents KWP (21, 1A, 18 02, 10 C0 / 10 81, 3E). Tout le
reste est refusé avant l'envoi, en particulier 10 02 (programmation), 27 (déverrouillage), 2E (écriture), 2F
(actionneurs), 31 (routines), 11 (redémarrage), 14 / 04 (effacement des défauts). Lignes CAN identiques à
`obd_scan.py` dans clio-iv-obd-utils.

## Courbes

Chaque tuile porte une mini-courbe des 2 dernières minutes (durée réglable dans Réglages), sauf les mesures
presque constantes (pression atmo, température extérieure, éthanol, niveau de carburant…). Un clic sur une tuile
ouvre sa courbe détaillée : 1 min, 5 min, 15 min ou trajet entier, mise à jour en direct sur le trajet en cours, et
une liste pour revoir les trajets passés de la voiture. Le curseur donne la valeur et l'heure exactes ; les seuils
d'alerte de la métrique sont tracés quand ils tombent dans l'échelle. La courbe est coupée là où le calculateur
n'a pas répondu. Échap ou un clic à côté pour fermer.

La tuile **Conso. instantanée** (L/100 km, étiquette « calculé ») vaut débit de carburant ÷ vitesse × 100, comme
l'ordinateur de bord : elle est enregistrée avec les autres mesures (sa courbe se revoit sur les trajets passés),
plafonnée à 99 et vide sous 5 km/h.

## Coût des trajets

Sur l'écran central du mode Conduite (et dans les coins du compte-tours en mode Détail) : la conso instantanée et
la moyenne du trajet en cours, et le **coût du trajet**, qui monte en direct (carburant consommé × prix au litre),
avec les litres et les kilomètres. Le carburant consommé est le débit (L/h) intégré sur le trajet : OBD standard,
ou sur la Clio la mesure Renault `22 2806` une fois validée dans l'onglet **Étalonnage** (sans elle, ni conso ni
coût).

Le prix au litre se règle dans **Réglages → Carburant**, un par voiture : la carte montre les deux voitures côte
à côte (logo, carburant : gazole pour la Clio, E85 ou SP95 pour la Fiesta), avec leur prix, quand et par qui il a
été réglé (ce dashboard, un autre PC ou la tablette, la page du serveur), et un témoin ; la voiture branchée (ou
affichée) est encadrée. Pour le changer : choisir la voiture, taper le prix, **Valider**. Le prix part tout de suite
au serveur et fait foi : les autres dashboards et la page du serveur le reprennent (dans la minute). Une
notification le confirme (« Prix 1,659 €/L modifié pour la Renault Clio IV dCi 90 · envoyé au serveur ») ; serveur
injoignable, le prix est gardé ici et part dès qu'il répond. Réglé sur la page du serveur (Serveur → Prix du
carburant) ou sur un autre dashboard, il revient ici. Le plus récemment réglé gagne.

| Témoin | Sens |
|---|---|
| vert « serveur » | le même que sur le serveur : il fait foi pour tous les dashboards |
| orange « en attente » | validé ici, pas encore reçu par le serveur (il part dès qu'il répond) |
| gris « hors ligne » | serveur injoignable ou pas réglé : dernier prix connu |

Le même témoin suit le coût du trajet sur l'écran central du mode Conduite (« 1,659 €/L · serveur »). Chaque
trajet garde le prix réglé pendant qu'il roulait : changer le prix au plein suivant ne touche pas les trajets
finis. Les trajets d'avant le premier prix réglé prennent celui-ci. L'onglet **Trajets** donne pour chacun le coût, les litres, la
moyenne en L/100 km et le prix retenu, et ce que le trajet a coûté en plus (ou en moins) qu'à la conso habituelle
(médiane des trajets d'au moins 3 km).

## Éco-conduite

Chaque trajet d'au moins 1 km reçoit un **score sur 100** : on part de 100 et chaque défaut retire des points
(plafonnés), affichés à côté de chaque ligne dans l'onglet **Trajets**. Les seuils propres au moteur sont dans
le module de la voiture (`SHIFT_RPM`, `TOP_GEAR_RATIO`, `PEDAL_HEAVY`).

| Défaut | Détecté par | Points perdus |
|---|---|---|
| accélération brusque | plus de 2 m/s² sur 2 s (départ à 0 → 20 km/h en 2,5 s) | 5 par tranche de 10 km, max 25 |
| freinage brusque | moins de -2,5 m/s² sur 2 s | 5 par tranche de 10 km, max 25 |
| rapports montés tard | au-delà de 2500 tr/min (Fiesta) ou 2000 tr/min (Clio) hors dernier rapport (déduit de régime ÷ vitesse) | 10 % du temps : 15, max 20 |
| pied lourd | pédale à plus de 70 % de sa course (jamais sous régulateur : la pédale reste au repos) | 10 % du temps : 15, max 15 |
| ralenti prolongé | moteur allumé à l'arrêt plus d'une minute | 20 % du temps moteur : 10, max 10 |

Les rapports de la Clio (régime ÷ vitesse de chaque rapport, `clio.GEARS`) sont estimés : à vérifier sur les
trajets enregistrés, comme ils l'ont été pour la Fiesta.

Sur la Fiesta, la pédale est donnée en course : 0 au repos, 100 au plancher. Le calculateur, lui, la donne en % de
la tension du capteur, 15,7 % au repos et 80 % au plancher : c'est converti à la lecture (`fiesta.pedal_travel`),
et les mesures enregistrées avant ont été converties au passage au format 4 de la base (la vérification de la
synchro corrige aussi celles du serveur).

S'y ajoutent, sans compter dans le score : le temps et le carburant (et son coût) à l'arrêt moteur allumé, la
part du temps en frein moteur (injection coupée, débit < 0,2 L/h au-dessus de 20 km/h), et le **régulateur** :
pédale au repos au-dessus de 50 km/h pendant au moins 10 s, moteur qui injecte au moins la moitié du temps
(sinon c'est un lever de pied). Temps, km et conso au régulateur, comparée à celle au-dessus de 50 km/h sans lui.
En haut de l'onglet, le bilan des trajets : score moyen pondéré par les km, coût total et aux 100 km, coût du
ralenti, et le point faible (le défaut qui coûte le plus de points) avec un conseil. Seuils et poids communs en
haut de `src/trajets.py`.

## Trajets

Le principe : **les mesures brutes ne sont jamais modifiées, les trajets sont calculés par-dessus.**

| Niveau | C'est quoi | Détecté par |
|---|---|---|
| session | une connexion au calculateur (contact mis) | le calculateur répond |
| marche moteur | le moteur tourne | régime > 400 tr/min (un régime bas de moins de 3 s ne coupe pas) |
| trajet | marches moteur séparées de moins de 5 min | la voiture a roulé (> 5 km/h) ; sinon « moteur allumé sans rouler » |

Un trajet est clos quand le moteur est arrêté depuis 5 min. Il porte un identifiant unique (UUID), un nom lisible
(`2026-10-06 · trajet 1 · 08:01`, compteur remis à 1 chaque jour), une fiche (durée, distance calculée depuis la
vitesse, carburant consommé et prix au litre, démarrage à froid ou à chaud, maximums, arrêts moteur, part sans
réponse, VIN, éco-conduite) et une note libre saisie dans l'onglet **Trajets**. Les événements (démarrage, arrêt
moteur, passage d'un seuil d'alerte) sont gardés à part. L'onglet montre les trajets de la voiture en cours.

### Mode sport et tronçons

Étiquettes pour l'apprentissage, enregistrées avec les mesures et exportées par le serveur en colonnes du CSV :

- **Sport** (gros bouton du mode Conduite) : `sport` = 1 en conduite sportive, 0 sinon, chaque seconde ;
- **tronçons** : `road` (1 ville, 2 route, 3 voie rapide, 4 bouchons) et `section` (numéro dans le trajet),
  chaque seconde ; `troncon` à chaque début. Un tronçon commence par un appui sur une touche de type de route
  (Annuler pendant 15 s), ou tout seul en passant un **point de passage** (tablette avec GPS) : réglés sur la
  page du serveur (depuis la trace d'un trajet, ils retiennent le sens de passage), repris à chaque synchro et
  gardés dans `data/points.json` pour rouler hors réseau. Un appui dans la minute d'avant passe devant ;
- **bouchons détectés en roulant** : sur un tronçon Route ou Voie rapide, sous 30 km/h 90 % du temps sur 1 min 30
  en avançant au moins 30 % du temps (ni un feu rouge, ni une pause), un tronçon Bouchons commence, daté du vrai
  début du ralentissement ; le type d'avant reprend quand on roule au-dessus de 45 km/h pendant 30 s. Seuils en
  haut de `src/etat.py` (`JAM_…`).

Après le trajet, la page du serveur montre la trace sur une carte et permet de corriger les tronçons, ou de les
déterminer d'après OpenStreetMap ; les tronçons corrigés remplacent ceux d'ici dans son CSV.

Les seuils sont en haut de `src/trajets.py`. Après un changement, tout se recalcule depuis le brut, identifiants
et notes gardés :

```bash
python src/trajets.py --voiture clio --rebuild      # liste aussi les trajets
```

## Données

Une base SQLite par voiture, `data/clio.db` et `data/fiesta.db`. L'ELM327 interroge les PID l'un après l'autre :
chaque valeur porte l'instant exact où le calculateur a répondu, pas l'heure de la boucle. Une non-réponse est
gardée (valeur vide) : on sait quand un capteur a décroché. La suralimentation calculée prend l'horodatage de la
pression d'admission, la conso instantanée celui du débit de carburant. Écriture groupée une fois par seconde,
environ 3 Mo par heure de route.

| Table | Contenu |
|---|---|
| `sessions` | brut : `id` (UUID), `start_ms`, `end_ms`, `source` (adaptateur), `vin`, `version` (commit git), `metrics` |
| `samples` | brut : `session_id`, `t_ms` (ms depuis 1970, UTC), `key` (`rpm`, `coolant`… comme `METRICS`, plus `boost` et `l100`), `value` |
| `trips` | calculé : un trajet, sa fiche et sa note (voir plus haut) |
| `events` | calculé : `t_ms`, `kind` (`demarrage`, `arret_moteur`, `alerte`), `key`, `value`, `detail` |

Une base à un ancien format est convertie toute seule au premier lancement (trajets recalculés, identifiants et
notes gardés). L'heure est celle de l'ordi : il doit être à l'heure. La version enregistrée est le commit git du
script (`CAR_VERSION` dans l'environnement, sans dossier `.git`).

```bash
sqlite3 data/clio.db "SELECT datetime(t_ms/1000, 'unixepoch', 'localtime'), key, value
                      FROM samples WHERE key = 'coolant' ORDER BY t_ms DESC LIMIT 10"
```

API (voiture en cours) : `/api/trips` (trajets avec leur fiche et leurs alertes), `POST /api/trips/note`
(`{"id": …, "note": …}`), `/api/history?key=rpm[&trip=ID][&from=MS&to=MS]` (points `[t_ms, valeur]` du dernier
trajet par défaut, moyennés par tranches au-delà de 2000 points). `/api/state` donne aussi `car` (`clio`,
`fiesta`), `ts`, l'horodatage de la dernière valeur de chaque métrique, et `trip`, l'identifiant du trajet en
cours ; `/api/meta` décrit la voiture (`car` : nom, constructeur, jauges, rapports, carburant). `POST
/api/settings` avec `{"car": "clio"}` change la voiture affichée (refusé si une autre voiture est branchée).

## Limites

- Avec un ELM327, les requêtes passent l'une après l'autre : la fréquence dépend du groupage accepté par
  l'adaptateur (voir « Vitesse de lecture »).
- Clio : la température d'huile et la tension batterie sont rarement dispo en OBD standard chez Renault : elles
  sont lues en UDS si le calculateur les donne. Pas de rapport engagé lu : il est déduit du régime et de la vitesse.
- Fiesta, taux d'éthanol (PID 52) : le relevé OBDb 2022 vient d'une Fiesta essence (01 51 → « Gasoline »), il ne
  dit donc rien de la FlexiFuel. À la connexion, le script écrit au journal le type de carburant annoncé (01 51,
  aussi au survol du témoin « Calculateur ») et interroge 01 52 même si le calculateur ne l'annonce pas : la
  tuile apparaît s'il répond. Sinon, aucun DID Ford connu ne le remplace pour l'instant (à chercher avec un scan
  UDS après un plein d'E85). Mesures Ford sans relevé sur une 1.0 EcoBoost FlexiFuel : à valider dans l'onglet
  Étalonnage.
- Une troisième voiture, ou une voiture ni Renault ni Ford, n'est pas reconnue : le dashboard garde la dernière
  vue (le journal le signale).

## Serveur

[clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) tourne à la maison et garde
l'historique de tous les trajets des deux voitures. On donne son adresse au dashboard ; avec plusieurs adresses
(séparées par des virgules), la première qui répond est prise : la maison en direct sur le Wi‑Fi, sinon le VPN
WireGuard sur la route.

```bash
python src/car_sentinel.py --server http://192.168.1.13:8207,http://10.100.0.1:8207
```

Toutes les 60 s, le dashboard envoie au serveur ce qu'il n'a pas encore de **chaque base** (une par voiture, chacune
une origine à part, déclarée « clio » ou « fiesta » ; le serveur vérifie avec le VIN), par paquets, puis les trajets
s'ils ont changé. Entre deux, il vérifie toutes les 3 s que le serveur répond ; hors réseau, il réessaie sans
bruit et envoie dès que le serveur revient. Un envoi coupé est refait au suivant. Un témoin en haut du dashboard
le montre : vert « serveur · adresse » quand il répond (avec l'adresse prise : Wi‑Fi ou VPN), rouge « serveur
injoignable » sinon (la raison au survol), gris pendant le premier essai. Avec les bases part l'étalonnage de
chaque voiture (voir « Étalonnage ») et son prix du carburant (voir « Coût des trajets ») : un client neuf les
reprend du serveur. Chaque vérification est une présence
(`POST /api/hello`, avec la voiture en cours) : la page du serveur affiche ce PC, son adresse et l'état de
l'ELM327. Les bases locales ne sont jamais modifiées, et celles de la démo ne partent jamais. HTTP simple, sans
jeton : le serveur n'est joignable que depuis la maison et le VPN.

| `car.conf` | Option | Rôle |
|---|---|---|
| `SERVEUR` | `--server` | adresse(s) du serveur, séparées par des virgules |
| `NOM_PC` | `--server-name` | nom de ce PC sur la page du serveur (défaut : nom de la machine) |

- Service : `SERVEUR=192.168.1.13,10.100.0.1` dans `car.conf` (port 8207 par défaut ; `http://IP:PORT` aussi
  accepté), puis `./service.sh restart` (Windows : `service.ps1 restart`). Serveur sur la même machine :
  `http://127.0.0.1:8207`. L'onglet Réglages → Serveur passe devant `car.conf` une fois modifié.
- Envoi à la main, sans le dashboard : `python src/synchro.py --voiture clio --server ...`.
