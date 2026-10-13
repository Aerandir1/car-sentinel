# Fiesta Sentinel

Dashboard OBD temps réel pour la **Ford Fiesta VII (2022) 1.0 EcoBoost 95 FlexiFuel ST-Line** (3 cylindres
essence turbo, injection directe, bicarburation essence / bioéthanol E85, boîte manuelle), sur PC portable, via
un adaptateur ELM327 branché sur la prise OBD. `src/dashboard_fiesta.py` lit le calculateur moteur (PCM) et sert
le dashboard sur http://fiesta-sentinel.localhost:8082 (fonctionne hors ligne), plus une API JSON (`/api/state`,
`/api/meta`, `/api/settings`).

Copie adaptée de [clio-sentinel](https://github.com/Aerandir1/clio-sentinel) (Clio IV dCi 90). Ce qui change :
- les mesures (`src/etat.py`) : celles d'un moteur essence turbo flexifuel (taux d'éthanol, richesse,
  corrections de carburant, avance, catalyseur, niveau de carburant…) à la place de l'EGR et du rail diesel ;
- `src/ford.py` remplace `renault.py` : mesures Ford en UDS (pression turbo avant papillon, température de
  culasse, air de suralimentation, wastegate, cliquetis, adaptation octane…), d'après
  [OBDb Ford-Fiesta](https://github.com/OBDb/Ford-Fiesta) ;
- compte-tours jusqu'à 7000 tr/min (zone rouge 6300), jauge turbo de -0,8 à 1,6 bar (dépression au ralenti) ;
- la démo simule ce moteur (5 rapports, E85 à 72 %) ;
- nom, port (8082, pour tourner à côté de la Clio sur 8080 / 8081), service `fiesta-sentinel`, `fiesta.conf`,
  base `data/fiesta.db`.

**Un seul adaptateur pour deux voitures** : si le service de la Clio tourne sur ce PC, c'est lui qui prend
l'ELM327 en premier (le dashboard Fiesta affiche alors « occupé »). Dans la Fiesta : `systemctl --user stop
clio-sentinel` avant de lancer celui-ci (et inversement).

Autour (mêmes services que la Clio) :
- [clio-hub](https://github.com/Aerandir1/clio-hub) : la page d'accueil 3D ; elle lit `/api/state` (port à adapter) ;
- [clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) : le serveur de la maison, qui
  accepte aussi la Fiesta (autre origine, autre VIN) si `SERVEUR` est rempli dans `fiesta.conf`.

```
src/
  dashboard_fiesta.py   point d'entrée : options, lancement de l'acquisition et du serveur
  etat.py             liste des métriques (METRICS), état partagé, CSV
  acquisition.py      lecture de l'ECU par l'ELM327
  ford.py             mesures Ford (UDS, lecture seule) quand l'OBD standard ne les donne pas
  serveur.py          serveur HTTP : page, logos, API JSON
  config.py           lecture de fiesta.conf
  reseau.py           adresses de la page (ce PC et réseau)
  demo.py             mode démo : trajet simulé
  donnees.py          base SQLite : sessions, mesures horodatées, trajets, événements
  trajets.py          découpage des mesures en trajets, fiche de chaque trajet
  synchro.py          envoi de la base vers clio-sentinel-server
web/dashboard.html    le dashboard
data/                 les bases SQLite (pas dans git)
fiesta.conf.exemple     modèle des réglages : copié en fiesta.conf (pas dans git) au premier lancement
lancer.sh             lancement en local (venv + navigateur)
service.sh            enregistrement permanent : service systemd lancé avec la session
Dockerfile, docker-compose.yml
```

## Installation (Arch)

```bash
sudo usermod -aG uucp $USER          # accès au port de l'ELM327 (dialout sous Debian / Ubuntu), puis déconnexion/reconnexion
python -m venv ~/.venvs/obd && source ~/.venvs/obd/bin/activate
pip install -r requirements.txt
```

## Réglages : fiesta.conf

Tous les réglages sont dans **`fiesta.conf`**, à la racine du projet, lu par le service, `lancer.sh` et Docker. Il
est créé au premier lancement depuis `fiesta.conf.exemple`, qui explique chaque ligne :

| Réglage | Rôle |
|---|---|
| `RESEAU` | `oui` : page accessible depuis les autres appareils du réseau (voir « Accès depuis le réseau ») |
| `PORT_PAGE` | port de la page (défaut 8082) |
| `ADAPTATEUR`, `DEBIT` | port de l'ELM327 (vide : auto-détection) : `/dev/ttyACM0`, `socket://192.168.0.10:35000`… |
| `SERVEUR`, `NOM_PC` | envoi vers clio-sentinel-server (voir « Serveur ») |
| `AUTRES_OPTIONS` | autres options du script (voir « Utilisation »), ex. `--fast --log logs/trajet.csv` |
| `DOCKER_ECOUTE`, `OBD_GID`, `FIESTA_DATA` | Docker seulement (voir « Docker ») |

Après un changement : `./service.sh restart` (service), `docker compose --env-file fiesta.conf up -d` (Docker).
Une option donnée en ligne de commande passe avant `fiesta.conf`.

## Adresse de la page

Sur ce PC, la page est servie en **http://fiesta-sentinel.localhost:8082** (HTTP simple, pas de certificat).
Chromium et Firefox envoient tout nom en `.localhost` vers l'ordi lui-même : rien à mettre dans `/etc/hosts`.
http://127.0.0.1:8082 et http://localhost:8082 redirigent vers ce nom (l'API, elle, répond aussi sur 127.0.0.1,
pour le hub).

## Docker sous Linux

```bash
[ -f fiesta.conf ] || cp fiesta.conf.exemple fiesta.conf
# groupe des ports série de l'hôte (dialout sous Debian / Ubuntu, uucp sous Arch) : droit d'ouvrir l'adaptateur
sed -i "s/^OBD_GID=.*/OBD_GID=$(getent group dialout uucp | head -n 1 | cut -d: -f3)/" fiesta.conf
docker compose down                     # si un ancien conteneur tourne encore
docker compose --env-file fiesta.conf up -d --build        # puis http://fiesta-sentinel.localhost:8082
```

`--env-file fiesta.conf` est à donner à chaque `docker compose up` : c'est lui qui donne à Docker le port publié, le
groupe du port série et l'emplacement de la base. Le conteneur reçoit les autres réglages de `fiesta.conf` tout seul.

Le conteneur démarre même sans la clé : il voit le `/dev` de l'hôte en lecture seule (sous `/hostdev`) et
attend que l'ELM327 apparaisse : avec `ADAPTATEUR` vide, il cherche dans `ttyUSB*`, `ttyACM*` et `rfcomm*` celui
qui répond à `ATI` (`docker compose logs` : « Adaptateur trouvé : ELM327 v1.5 sur /hostdev/ttyACM0 »). Un port
donné dans `ADAPTATEUR` (`/dev/ttyACM0`) est traduit tout seul en `/hostdev/ttyACM0`. Seuls les ports série USB
lui sont autorisés, pas les disques ni le reste. `OBD_GID` lui donne le droit d'ouvrir le port série : s'il est
faux, le journal donne la bonne valeur (« OBD_GID=985 dans fiesta.conf »).

La base est rangée dans `data/`, les CSV dans `logs/`. Avec `restart: unless-stopped`, le conteneur se
relance tout seul au démarrage de Docker : c'est l'équivalent de `service.sh`.

**Accès depuis le réseau** : dans `fiesta.conf`, `RESEAU=oui` et `DOCKER_ECOUTE=0.0.0.0`, puis
`docker compose --env-file fiesta.conf up -d`. Le conteneur ne voit pas les adresses de l'hôte et ne les affiche pas :
`ip -4 -br addr` les donne. Si `RESEAU=oui` mais que le port n'est publié que sur ce PC, le journal le signale
(`docker compose logs`).

## Docker sous Windows (Docker Desktop)

Tout se fait dans PowerShell, dans le dossier du projet. Docker Desktop ne voit pas les ports COM de Windows :
l'adaptateur passe soit par le Wi-Fi, soit par WSL2.

**1. Une seule fois.** Dans Docker Desktop, *Settings → General* : cocher « Start Docker Desktop when you sign
in ». Le conteneur se relancera alors tout seul à chaque démarrage de Windows. Puis créer les réglages :
```powershell
Copy-Item fiesta.conf.exemple fiesta.conf
notepad fiesta.conf
```

**2. La base dans un volume Docker.** Un dossier Windows (`C:\...`) partagé avec le conteneur est lent et
SQLite peut y renvoyer « database is locked ». Mettre donc `FIESTA_DATA=fiesta-data` dans `fiesta.conf` (volume Docker).
Pour en sortir une copie (pour l'ouvrir dans DB Browser for SQLite, par exemple) :

```powershell
docker compose stop
docker compose cp fiesta:/app/data .\data-copie     # fiesta.db + ses fichiers -wal et -shm : garder les trois ensemble
docker compose start
```

Variante : cloner le projet dans WSL (`\\wsl$\Ubuntu\home\...`) et suivre la section Linux.

**3. L'adaptateur**, dans `fiesta.conf` :

- **Wi-Fi** (PC connecté au Wi-Fi de l'adaptateur) : `ADAPTATEUR=socket://192.168.0.10:35000` et `DEBIT=38400`.
- **USB** : laisser `ADAPTATEUR` vide. Docker Desktop ne voit la clé que si elle est passée à WSL2 par
  [usbipd-win](https://github.com/dorssel/usbipd-win). Le script `windows-usb.ps1` s'en charge, une seule fois
  (clé branchée, Docker Desktop démarré) :
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\windows-usb.ps1
  ```
  Il installe usbipd, repère la clé, la partage, crée une tâche planifiée qui la rattache à WSL2 à chaque
  ouverture de session et à chaque branchement, puis vérifie que le conteneur la voit (et corrige `OBD_GID`).
  `-Supprimer` retire la tâche et le partage. À la main, en PowerShell admin :
  ```powershell
  winget install usbipd
  usbipd list                                        # BUSID de l'ELM327 : CH340, FTDI, « Périphérique série USB (COMx) »…
  usbipd bind --busid 2-3                            # une seule fois
  usbipd attach --wsl --busid 2-3 --auto-attach      # laisse la fenêtre ouverte : rattache la clé à chaque branchement
  ```
  Sans cette étape, le conteneur ne voit pas la clé (le journal le rappelle). Une fois attachée, il la trouve
  tout seul, sans redémarrer : `docker compose logs` affiche « Adaptateur trouvé : ELM327… ». S'il dit « pas le
  droit d'ouvrir », mettre dans `fiesta.conf` l'`OBD_GID` qu'il donne et relancer.
- **Bluetooth** (port COM virtuel) : passer par un pont RFC 2217 sous Windows (ex. `rfc2217_server.py` des
  exemples de pyserial), puis `ADAPTATEUR=rfc2217://host.docker.internal:2217`.

**4. Lancer** (dans un autre PowerShell pour l'USB) :
```powershell
$env:FIESTA_VERSION=(git describe --always)
docker compose --env-file fiesta.conf up -d --build
```
Ouvrir http://fiesta-sentinel.localhost:8082.
`docker compose logs -f` pour suivre le script, `docker compose down` pour l'arrêter (la base reste dans le volume).

Port 8082 déjà pris (« port is already allocated ») : `PORT_PAGE=8106` dans `fiesta.conf`, relancer, puis
http://fiesta-sentinel.localhost:8106.

**Accès depuis les autres PC du réseau** : `RESEAU=oui` et `DOCKER_ECOUTE=0.0.0.0` dans `fiesta.conf`, relancer, puis
ouvrir le port dans le pare-feu Windows (PowerShell admin, une seule fois, réseau privé seulement) :
```powershell
New-NetFirewallRule -DisplayName "Fiesta Sentinel" -Direction Inbound -Protocol TCP -LocalPort 8082 -Action Allow -Profile Private
```
Les autres PC ouvrent alors `http://<IP du PC>:8082` (IP donnée par `ipconfig`). Le dashboard n'a pas de mot
de passe : tout le réseau peut le voir et changer les réglages et les notes des trajets.

## Enregistrement permanent

```bash
./service.sh install     # le script tourne en fond et démarre avec l'ordi (une seule fois)
./service.sh restart     # après un changement dans fiesta.conf
./service.sh status      # tourne-t-il ? adresses de la page, ou l'erreur qui l'empêche de démarrer
./service.sh logs        # journal en direct
./service.sh stop        # arrêt (start pour relancer, remove pour supprimer le service)
./lancer.sh              # ouvre la page (relance le service s'il ne tourne pas)
```

Le service attend l'ELM327 et enregistre dès que le calculateur répond (contact mis). Contact coupé, plus de
réponse pendant 10 s : la session se ferme et le script attend la suivante. Ordi éteint en route : rien n'est
perdu, le trajet est clos au lancement suivant. Pour qu'il démarre avec l'ordi sans ouvrir de session :
`sudo loginctl enable-linger $USER`.

## Accès depuis le réseau (Raspberry Pi dans la voiture)

Par défaut la page n'est servie qu'à la machine elle-même. Avec **`RESEAU=oui`** dans `fiesta.conf` (puis
`./service.sh restart`), elle l'est aussi aux autres appareils du réseau (téléphone, tablette sur le tableau de
bord, PC). À la main : `python src/dashboard_fiesta.py --lan`.

Au lancement, le script affiche les adresses à ouvrir (`./service.sh status` les redonne) :
`http://<nom>.local:8082` (le nom de la machine, résolu par mDNS : avahi, installé d'office sur Raspberry Pi OS)
et `http://<IP>:8082` pour chaque adresse réseau.

- **Ça ne s'ouvre pas** : `./service.sh status` doit montrer les lignes « sur le réseau » ; les deux appareils
  doivent être sur le même Wi‑Fi (pas un réseau invité, qui isole les appareils) ; un pare-feu sur la machine
  (ufw, firewalld) doit laisser passer le port 8082.
- **Dans la voiture**, sans box : le Pi crée son propre Wi‑Fi, sur lequel se connectent téléphone et tablette.
  Avec NetworkManager (Raspberry Pi OS Bookworm) :
  ```bash
  sudo nmcli device wifi hotspot ifname wlan0 ssid Fiesta password "un-mot-de-passe"
  sudo nmcli connection modify Hotspot connection.autoconnect yes      # au démarrage du Pi
  ```
  Le Pi y a toujours l'adresse `10.42.0.1` : la page est sur http://10.42.0.1:8082 (adresse fixe, à mettre en
  favori ; tous les téléphones ne résolvent pas les noms `.local`). Pour le lancer au démarrage du Pi sans ouvrir
  de session : `RESEAU=oui` dans `fiesta.conf`, puis
  ```bash
  ./service.sh install
  sudo loginctl enable-linger $USER
  ```
- **Sécurité** : pas de mot de passe sur le dashboard ; tout appareil du réseau peut le voir et changer les
  réglages d'acquisition et les notes des trajets. À réserver au Wi‑Fi de la voiture ou de la maison, protégé
  par mot de passe, jamais un Wi‑Fi public.
- **Hub** : une page servie par la même machine sur un autre port (clio-hub) et ouverte par le même nom
  (`http://10.42.0.1:8000`) peut lire `/api/state`.

## Utilisation

```bash
python src/dashboard_fiesta.py --port /dev/ttyUSB0 --log trajet.csv  # à la main, contact mis
```

Les options ci-dessous passent avant `fiesta.conf` ; pour les garder, les mettre dans `AUTRES_OPTIONS`.

| Option | Rôle |
|---|---|
| `--port` | port série de l'ELM327 (auto-détection si absent) |
| `--db FICHIER.db` | base des mesures (défaut `data/fiesta.db`) |
| `--no-db` | n'enregistre rien |
| `--log FICHIER.csv` | enregistre les mesures en CSV |
| `--log-hz` | fréquence du CSV (défaut 5 Hz) |
| `--delay` | pause entre deux requêtes OBD (si l'ELM327 décroche) |
| `--http` | port de la page web (défaut 8082) |
| `--bind` | adresse d'écoute (défaut 127.0.0.1 ; 0.0.0.0 dans Docker) |
| `--lan` | page accessible depuis les autres appareils du réseau (voir « Accès depuis le réseau ») |
| `--demo` | démarre en mode démo (trajet simulé, base à part `data/fiesta-demo.db`) |
| `--fast` | mode rapide python-obd : requêtes plus courtes (certains clones ELM327 le refusent) |
| `--max-hz` | fréquence max d'acquisition (défaut 20) |
| `--server URL` | envoie la base à clio-sentinel-server (voir « Serveur ») |

L'onglet **Paramètres** du dashboard règle tout ça à chaud :
- affichage (gardé dans le navigateur) : rafraîchissement de la page, durée du graphique ;
- acquisition (appliqué par le script jusqu'à son arrêt) : fréquence max, pause entre requêtes OBD, fréquence du CSV,
  mode rapide (le basculer relance la connexion à l'ELM327) ;
- **mode démo** (bouton en haut de l'onglet) : un trajet simulé en boucle (démarrage, ville, voie rapide, arrêt
  moteur), pour voir le dashboard sans voiture. Tout se passe comme avec la voiture (session, mesures, trajets,
  envoi au serveur), mais dans une base à part, `data/fiesta-demo.db`, que le serveur reçoit comme une autre origine
  (« <nom du PC> · démo », VIN `WF0DEMO…`). Les vrais trajets n'y sont jamais mêlés. Arrêter la démo reprend
  l'ELM327 ; supprimer `data/fiesta-demo.db` efface la démo locale.

Au démarrage, le script demande au calculateur quels PID il supporte et n'affiche que ceux-là.
D'après le relevé OBDb d'une Fiesta 2022, le PCM annonce en standard : régime, vitesse, températures eau /
admission / huile / extérieure / catalyseur, pression d'admission et atmo, charge, papillon, pédale, conso,
niveau de carburant, richesse commandée, corrections court / long terme, avance et tension. Il n'annonce pas le
débit d'air, la pression rail ni le taux d'éthanol (tuiles absentes si c'est bien le cas sur la voiture).

En plus, le script essaie les mesures Ford de `src/ford.py`. Ce sont des lectures UDS (service 0x22 sur 7E0),
qui n'écrivent jamais rien dans le calculateur ; chacune n'est gardée que si le calculateur répond (journal :
« Mesures Ford lues en plus de l'OBD standard »). Identifiants et échelles tirés de
[OBDb](https://github.com/OBDb/Ford-Fiesta) (CC BY-SA 4.0).

| Mesure | DID | Affichée |
|---|---|---|
| huile, tension, pédale, niveau carburant | `1310`, `1172`, `09D4`, `F42F` | d'office (seulement si l'OBD standard ne les donne pas) |
| température d'air de suralimentation | `0461` | d'office (vérifiée sur plusieurs Fiesta dans OBDb) |
| pression turbo (avant papillon) | `033E` | après validation (onglet Étalonnage) |
| température de culasse | `0334` | après validation |
| wastegate, consigne rail | `0462`, `03DC` | après validation |
| retrait cliquetis cyl. 1, adaptation octane | `03EC`, `03E8` | après validation |
| rapport engagé, vie de l'huile | `1E12`, `054B` | après validation |

Pour alléger ou compléter les mesures, édite la liste `METRICS` en haut de `src/etat.py` et `FALLBACK` dans
`src/ford.py`.

## Étalonnage

Les mesures Ford non vérifiées sur cette voiture sont lues et leur **valeur brute** est enregistrée (clé
`brut.<mesure>` dans la base), mais leur tuile n'apparaît qu'une fois validées dans l'onglet **Étalonnage** :

1. choisir la mesure ; la valeur brute et la valeur étalonnée (échelle OBDb) s'affichent en direct ;
2. comparer à une référence : pour la pression turbo, contact mis moteur arrêté elle doit valoir la pression
   atmo (≈ 100 kPa) ; la culasse suit la température d'eau ; le rapport doit coller à celui engagé ;
   « Noter l'instant » ou clic / glisser sur la courbe d'un trajet, puis la valeur de référence ;
3. « Calculer depuis les références » si l'échelle est fausse (une référence fixe le gain, deux le gain et le
   décalage) ; les curseurs affinent ;
4. « Enregistrer », puis « Valider : afficher la tuile ».

Le réglage est rangé dans `data/etalonnage.json` (à côté de la base). Comme la valeur brute est gardée, un
étalonnage corrigé s'applique aussi aux courbes des trajets passés.

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

Tout est dans une base SQLite, `data/fiesta.db`. L'ELM327 interroge les PID l'un après l'autre : chaque valeur
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
L'heure est celle de l'ordi : il doit être à l'heure. Dans Docker, `FIESTA_VERSION=$(git describe --always)`
devant `docker compose up` enregistre la version (l'image n'a pas le dossier `.git`).

```bash
sqlite3 data/fiesta.db "SELECT datetime(t_ms/1000, 'unixepoch', 'localtime'), key, value
                      FROM samples WHERE key = 'coolant' ORDER BY t_ms DESC LIMIT 10"
```

API : `/api/trips` (trajets avec leur fiche et leurs alertes), `POST /api/trips/note` (`{"id": …, "note": …}`),
`/api/history?key=rpm[&trip=ID][&from=MS&to=MS]` (points `[t_ms, valeur]` du dernier trajet par défaut, moyennés
par tranches au-delà de 2000 points). `/api/state` donne aussi `ts`, l'horodatage de la dernière valeur de chaque
métrique, et `trip`, l'identifiant du trajet en cours.

## Limites

- Avec un ELM327, les PID sont interrogés l'un après l'autre : quelques Hz au total.
- Le taux d'éthanol (PID 52) n'est pas annoncé par le PCM dans le relevé OBDb 2022 : s'il manque, aucun DID Ford
  connu ne le remplace pour l'instant (à chercher avec un scan UDS, comme clio-iv-obd-utils pour la Clio).
- Mesures Ford sans relevé sur une 1.0 EcoBoost FlexiFuel : à valider dans l'onglet Étalonnage.

## Serveur

[clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) tourne à la maison et garde
l'historique de tous les trajets. On donne son adresse au dashboard ; avec plusieurs adresses (séparées par des
virgules), la première qui répond est prise : la maison en direct sur le Wi‑Fi, sinon le VPN WireGuard sur la route.

```bash
python src/dashboard_fiesta.py --server http://192.168.1.13:8207,http://10.100.0.1:8207
```

Toutes les 60 s, le dashboard envoie au serveur les mesures qu'il n'a pas encore, par paquets, puis les trajets
s'ils ont changé. Entre deux, il vérifie toutes les 3 s que le serveur répond ; hors réseau, il réessaie sans
bruit et envoie dès que le serveur revient. Un envoi coupé est refait au suivant.
Un témoin en haut du dashboard le montre : vert « serveur · adresse » quand il répond (avec l'adresse prise : Wi‑Fi
ou VPN), rouge « serveur injoignable » sinon (la raison au survol), gris pendant le premier essai. Chaque
vérification est une présence (`POST /api/hello`) : la page du serveur affiche ce PC, son adresse et l'état de
l'ELM327. La base locale
n'est jamais modifiée. HTTP simple, sans jeton : le serveur n'est joignable que depuis la maison et le VPN.

| `fiesta.conf` | Option | Rôle |
|---|---|---|
| `SERVEUR` | `--server` | adresse(s) du serveur, séparées par des virgules |
| `NOM_PC` | `--server-name` | nom de ce PC sur la page du serveur (défaut : nom de la machine) |

- Service et Docker : `SERVEUR=192.168.1.13,10.100.0.1` dans `fiesta.conf` (port 8207 par défaut ; `http://IP:PORT` aussi accepté), puis
  `./service.sh restart` (ou `docker compose --env-file fiesta.conf up -d`). Serveur sur la même machine que le
  conteneur : `http://host.docker.internal:8207`.
- Envoi à la main, sans le dashboard : `python src/synchro.py --server ...`.
