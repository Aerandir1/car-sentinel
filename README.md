# Clio Sentinel

Dashboard OBD temps réel pour la Clio IV dCi 90 (moteur K9K), sur PC portable, via un adaptateur ELM327
branché sur la prise OBD. `src/dashboard_clio.py` lit l'ECU et sert le dashboard sur
http://clio-sentinel.localhost:8080
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
  renault.py          mesures Renault (UDS, lecture seule) quand l'OBD standard ne les donne pas
  etalonnage.py       gain et décalage des mesures Renault (onglet Étalonnage)
  diagnostic.py       onglet Diagnostic : codes défaut et scan UDS / KWP, lecture seule (liste blanche)
  serveur.py          serveur HTTP : page, logos, API JSON
  config.py           lecture de clio.conf
  reseau.py           adresses de la page (ce PC et réseau)
  demo.py             mode démo : trajet simulé
  donnees.py          base SQLite : sessions, mesures horodatées, trajets, événements
  trajets.py          découpage des mesures en trajets, fiche de chaque trajet
  synchro.py          envoi de la base vers clio-sentinel-server
  pont_web.py         secours : clé USB ouverte par la page web (Web Serial) et relayée au script
web/dashboard.html    le dashboard
data/                 les bases SQLite (pas dans git)
clio.conf.exemple     modèle des réglages : copié en clio.conf (pas dans git) au premier lancement
lancer.sh             Linux : lancement en local (venv + navigateur)
service.sh            Linux : enregistrement permanent, service systemd lancé avec la session
lancer.bat, lancer.ps1   Windows : lancement en local (Python + venv + navigateur), double-clic sur lancer.bat
service.ps1           Windows : enregistrement permanent, lancé à l'ouverture de session
windows-commun.ps1    Windows : fonctions communes aux deux scripts
```

Le script tourne directement sous **Linux** et sous **Windows** (Python 3.9 ou plus), sans Docker.

## Installation et lancement sous Linux

```bash
sudo usermod -aG uucp $USER          # accès au port de l'ELM327 (dialout sous Debian / Ubuntu), puis déconnexion/reconnexion
./lancer.sh                          # crée ~/.venvs/obd au premier lancement, lance le script et ouvre la page
```

À la main :
```bash
python -m venv ~/.venvs/obd && source ~/.venvs/obd/bin/activate
pip install -r requirements.txt
python src/dashboard_clio.py
```

Avec `ADAPTATEUR` vide, le script cherche dans `ttyUSB*`, `ttyACM*` et `rfcomm*` celui qui répond à `ATI`
(« Adaptateur trouvé : ELM327 v1.5 sur /dev/ttyACM0 ») et attend la clé si elle n'est pas branchée. S'il n'a pas
le droit d'ouvrir le port, le journal donne la commande `usermod` à taper.

## Installation et lancement sous Windows

Double-cliquer sur **`lancer.bat`** (ou, dans PowerShell, `.\lancer.bat`). Au premier lancement, il installe
Python si besoin (winget, pour l'utilisateur seulement), crée l'environnement `%USERPROFILE%\.venvs\obd`, y
installe les dépendances, puis lance le script et ouvre la page. La fenêtre reste ouverte tant que le script
tourne (la fermer, ou Ctrl+C, l'arrête). Des options peuvent suivre : `.\lancer.bat --port COM3 --log trajet.csv`.

L'adaptateur, dans `clio.conf` :
- **USB** : laisser `ADAPTATEUR` vide. Le script essaie les ports COM, clés USB d'abord (CH340, FTDI, CP210x,
  Prolific), et garde celui qui répond à `ATI` (« Adaptateur trouvé : ELM327 v1.5 sur COM3 »). Si Windows ne
  reconnaît pas la clé (pas de port COM dans le Gestionnaire de périphériques), installer le pilote de sa puce
  (souvent CH340). `ADAPTATEUR=COM3` force un port.
- **Bluetooth** : appairer l'adaptateur dans Windows, qui lui donne un port COM ; il est trouvé comme une clé USB
  (plus lentement), ou donné par `ADAPTATEUR=COM5`.
- **Wi-Fi** (PC connecté au Wi-Fi de l'adaptateur) : `ADAPTATEUR=socket://192.168.0.10:35000` et `DEBIT=38400`.

Un port COM ne s'ouvre que par un seul programme : fermer les autres logiciels OBD avant (le témoin indique
« port occupé » sinon).

Port 8080 déjà pris : `PORT_PAGE=8106` dans `clio.conf`, relancer, puis http://clio-sentinel.localhost:8106.

**Accès depuis les autres PC du réseau** : `RESEAU=oui` dans `clio.conf`, relancer, puis ouvrir le port dans le
pare-feu Windows (PowerShell admin, une seule fois, réseau privé seulement) :
```powershell
New-NetFirewallRule -DisplayName "Clio Sentinel" -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Private
```

## Réglages : clio.conf

Tous les réglages sont dans **`clio.conf`**, à la racine du projet, lu par le script à chaque lancement. Il
est créé au premier lancement depuis `clio.conf.exemple`, qui explique chaque ligne :

| Réglage | Rôle |
|---|---|
| `RESEAU` | `oui` : page accessible depuis les autres appareils du réseau (voir « Accès depuis le réseau ») |
| `PORT_PAGE` | port de la page (défaut 8080) |
| `ADAPTATEUR`, `DEBIT` | port de l'ELM327 (vide : auto-détection) : `/dev/ttyACM0`, `COM3`, `socket://192.168.0.10:35000`… |
| `SERVEUR`, `NOM_PC` | envoi vers clio-sentinel-server (voir « Serveur ») |
| `AUTRES_OPTIONS` | autres options du script (voir « Utilisation »), ex. `--fast --log logs/trajet.csv` |

Après un changement : relancer le script (`./service.sh restart` ou `service.ps1 restart` pour le service).
Une option donnée en ligne de commande passe avant `clio.conf`.

## Adresse de la page

Sur ce PC, la page est servie en **http://clio-sentinel.localhost:8080** (HTTP simple, pas de certificat).
Chrome, Edge et Firefox envoient tout nom en `.localhost` vers l'ordi lui-même : rien à mettre dans le fichier hosts.
http://127.0.0.1:8080 et http://localhost:8080 redirigent vers ce nom (l'API, elle, répond aussi sur 127.0.0.1,
pour le hub).

## Enregistrement permanent

Linux (service systemd de l'utilisateur) :
```bash
./service.sh install     # le script tourne en fond et démarre avec l'ordi (une seule fois)
./service.sh restart     # après un changement dans clio.conf
./service.sh status      # tourne-t-il ? adresses de la page, ou l'erreur qui l'empêche de démarrer
./service.sh logs        # journal en direct
./service.sh stop        # arrêt (start pour relancer, remove pour supprimer le service)
./lancer.sh              # ouvre la page (relance le service s'il ne tourne pas)
```
Pour qu'il démarre avec l'ordi sans ouvrir de session : `sudo loginctl enable-linger $USER`.

Windows (raccourci dans le dossier Démarrage : sans fenêtre, à chaque ouverture de session, sans droits admin) :
```powershell
powershell -ExecutionPolicy Bypass -File .\service.ps1 install    # une seule fois
powershell -ExecutionPolicy Bypass -File .\service.ps1 restart    # après un changement dans clio.conf
powershell -ExecutionPolicy Bypass -File .\service.ps1 status     # tourne-t-il ? adresses, ou l'erreur
powershell -ExecutionPolicy Bypass -File .\service.ps1 logs       # journal en direct (logs\clio.log)
powershell -ExecutionPolicy Bypass -File .\service.ps1 stop       # arrêt (start pour relancer, remove pour supprimer)
.\lancer.bat                                                      # ouvre la page (relance le script s'il ne tourne pas)
```

Le script attend l'ELM327 et enregistre dès que le calculateur répond (contact mis). Contact coupé, plus de
réponse pendant 10 s : la session se ferme et le script attend la suivante. Ordi éteint en route : rien n'est
perdu, le trajet est clos au lancement suivant.

## Accès depuis le réseau (Raspberry Pi dans la voiture)

Par défaut la page n'est servie qu'à la machine elle-même. Avec **`RESEAU=oui`** dans `clio.conf` (puis
`./service.sh restart`), elle l'est aussi aux autres appareils du réseau (téléphone, tablette sur le tableau de
bord, PC). À la main : `python src/dashboard_clio.py --lan`.

Au lancement, le script affiche les adresses à ouvrir (`./service.sh status` les redonne) :
`http://<nom>.local:8080` (le nom de la machine, résolu par mDNS : avahi, installé d'office sur Raspberry Pi OS)
et `http://<IP>:8080` pour chaque adresse réseau.

- **Ça ne s'ouvre pas** : `./service.sh status` doit montrer les lignes « sur le réseau » ; les deux appareils
  doivent être sur le même Wi‑Fi (pas un réseau invité, qui isole les appareils) ; un pare-feu sur la machine
  (ufw, firewalld) doit laisser passer le port 8080.
- **Dans la voiture**, sans box : le Pi crée son propre Wi‑Fi, sur lequel se connectent téléphone et tablette.
  Avec NetworkManager (Raspberry Pi OS Bookworm) :
  ```bash
  sudo nmcli device wifi hotspot ifname wlan0 ssid Clio password "un-mot-de-passe"
  sudo nmcli connection modify Hotspot connection.autoconnect yes      # au démarrage du Pi
  ```
  Le Pi y a toujours l'adresse `10.42.0.1` : la page est sur http://10.42.0.1:8080 (adresse fixe, à mettre en
  favori ; tous les téléphones ne résolvent pas les noms `.local`). Pour le lancer au démarrage du Pi sans ouvrir
  de session : `RESEAU=oui` dans `clio.conf`, puis
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
python src/dashboard_clio.py --port /dev/ttyUSB0 --log trajet.csv  # à la main, contact mis (Windows : --port COM3)
```

Les options ci-dessous passent avant `clio.conf` ; pour les garder, les mettre dans `AUTRES_OPTIONS`.

| Option | Rôle |
|---|---|
| `--port` | port série de l'ELM327 (auto-détection si absent) |
| `--db FICHIER.db` | base des mesures (défaut `data/clio.db`) |
| `--no-db` | n'enregistre rien |
| `--log FICHIER.csv` | enregistre les mesures en CSV |
| `--log-hz` | fréquence du CSV (défaut 5 Hz) |
| `--delay` | pause entre deux requêtes OBD (si l'ELM327 décroche) |
| `--http` | port de la page web (défaut 8080) |
| `--bind` | adresse d'écoute (défaut 127.0.0.1) |
| `--lan` | page accessible depuis les autres appareils du réseau (voir « Accès depuis le réseau ») |
| `--demo` | démarre en mode démo (trajet simulé, base à part `data/clio-demo.db`) |
| `--fast` | mode rapide python-obd : requêtes plus courtes (certains clones ELM327 le refusent) |
| `--max-hz` | fréquence max d'acquisition (défaut 20) |
| `--server URL` | envoie la base à clio-sentinel-server (voir « Serveur ») |

L'onglet **Paramètres** du dashboard règle tout ça à chaud :
- affichage (gardé dans le navigateur) : rafraîchissement de la page, durée du graphique ;
- acquisition (appliqué par le script jusqu'à son arrêt) : fréquence max, pause entre requêtes OBD, fréquence du CSV,
  mode rapide (le basculer relance la connexion à l'ELM327) ;
- **mode démo** (bouton en haut de l'onglet) : un trajet simulé en boucle (démarrage, ville, voie rapide, arrêt
  moteur), pour voir le dashboard sans voiture. Tout se passe comme avec la voiture (session, mesures, trajets,
  envoi au serveur), mais dans une base à part, `data/clio-demo.db`, que le serveur reçoit comme une autre origine
  (« <nom du PC> · démo », VIN `VF1DEMO…`). Les vrais trajets n'y sont jamais mêlés. Arrêter la démo reprend
  l'ELM327 ; supprimer `data/clio-demo.db` efface la démo locale.

Au démarrage, le script demande au calculateur quels PID il supporte et n'affiche que ceux-là.
Pour une mesure absente en OBD standard, il essaie l'équivalent Renault (`src/renault.py`) : température d'huile
(`22 2007`), tension d'alimentation du calculateur (`22 2005`), pédale d'accélérateur (`22 202E`). Ce sont des
lectures UDS (service 0x22), qui n'écrivent jamais rien dans le calculateur ; chacune n'est gardée que si le
calculateur répond (journal : « Mesures Renault lues en plus de l'OBD standard »).
Identifiants tirés de [OBDb](https://github.com/OBDb/Renault-Clio) (CC BY-SA 4.0).
Pour alléger ou compléter les mesures, édite la liste `METRICS` en haut de `src/etat.py`.

## Étalonnage

Trois autres mesures Renault, trouvées par le scan UDS (clio-iv-obd-utils), ont une échelle inconnue :
pression atmo (`22 2009`), consigne EGR (`22 2408`), débit carburant (`22 2806`). Elles sont lues et leur
**valeur brute** est enregistrée (clé `brut.<mesure>` dans la base), mais leur tuile n'apparaît qu'une fois
étalonnées et validées dans l'onglet **Étalonnage** :

1. choisir la mesure ; la valeur brute et la valeur étalonnée s'affichent en direct ;
2. noter des références : « Noter l'instant » (en roulant, par le passager) ou clic / glisser sur la courbe d'un
   trajet enregistré, puis la valeur lue sur l'ordinateur de bord (la conso en L/100 km ou en L/h) ;
3. « Calculer depuis les références » : une référence fixe le gain, deux ou plus (à des régimes différents) le
   gain et le décalage ; les curseurs affinent, la courbe et l'écart de chaque référence suivent ;
4. « Enregistrer », puis « Valider : afficher la tuile ».

Le réglage est rangé dans `data/etalonnage.json` (à côté de la base). Comme la valeur brute est gardée, un
étalonnage corrigé s'applique aussi aux courbes des trajets passés. Les mesures déjà vérifiées (huile, tension,
pédale) peuvent y être réajustées de la même façon.

## Diagnostic

L'onglet **Diagnostic** lit les codes défaut et fouille les identifiants des calculateurs, sans jamais rien
écrire ni effacer. Pendant un diagnostic, le dashboard laisse l'ELM327 (témoins « diagnostic ») et
l'enregistrement est en pause ; il reprend tout seul après.

**Véhicule** : choisi en haut de l'onglet (gardé par le navigateur). Requêtes, calculateurs et session suivent ce
choix :

- **Renault Clio IV dCi 90 (K9K)** : prise OBD en CAN, requêtes UDS (détail ci-dessous).
- **Renault Clio II phase 2 (2002) essence** : prise OBD en K-line, sans CAN. Codes défaut OBD 03 / 07, puis
  KWP 18 02 sur l'injection (adresses 7A Renault et 10 OBD, init 5 bauds ou rapide comme en OBD) ; codes
  Renault laissés en hexa. Scan des identifiants locaux (KWP 21, 01 à FF), session de diagnostic Renault 10 C0,
  retour en 10 81. Les autres calculateurs (ABS, airbag, UCH) ne sont pas proposés ; « Autre adresse » permet
  d'en viser un. Un calculateur seul (sur table) qui ne répond pas en OBD est quand même interrogé en KWP ;
  chaque diagnostic laisse le journal de ses échanges dans `data/diag/*.log`.
  Si le protocole détecté ne correspond pas au véhicule choisi, le diagnostic s'arrête avant toute requête
  constructeur.

- **Codes défaut** : OBD 03 (mémorisés) et 07 (en attente) à tous les calculateurs, puis UDS 19 02 sur le moteur
  (7E0) et le tableau de bord (743). Contact mis suffit.
- **Scan des identifiants** : lecture (UDS 22) de chaque DID d'une plage, sur le moteur, le tableau de bord ou une
  autre adresse. Les DID qui répondent sont rangés dans `data/diag/*.json`, au format de `uds_scan.py`
  (clio-iv-obd-utils). Cocher deux scans ou plus et « Comparer » liste ce qui change : faire un scan contact mis,
  un au ralenti, un à 2500 tr/min isole les mesures vivantes.
- **Session étendue (10 03)** : certains DID ne répondent qu'en session de diagnostic étendue, comme avec l'outil
  du garage. Moteur coupé (contact mis) et voiture à l'arrêt obligatoires : régime et vitesse sont lus juste avant,
  la session est refusée sinon. Retour en session normale (10 01) à la fin, même si le scan est arrêté ; sans
  message pendant ~5 s, le calculateur y revient de lui-même.

**Liste blanche** (`ALLOWED` dans `src/diagnostic.py`, vérifiée au seul endroit qui écrit sur le port) : seules
partent vers la voiture les lectures (OBD 01, 03, 07, 09 ; UDS 22, 19 02), l'entrée / sortie de session (10 03,
10 01) et son maintien (3E) ; en K-line, les équivalents KWP (21, 1A, 18 02, 10 C0 / 10 81, 3E). Tout le
reste est refusé avant l'envoi, en particulier 10 02 (programmation), 27
(déverrouillage), 2E (écriture), 2F (actionneurs), 31 (routines), 11 (redémarrage), 14 / 04 (effacement des
défauts). Lignes CAN identiques à `obd_scan.py` dans clio-iv-obd-utils. Le dashboard n'a pas de mot de passe : avec
`RESEAU=oui`, tout appareil du réseau peut lancer un diagnostic (en lecture seule).

## Courbes

Chaque tuile porte une mini-courbe des 2 dernières minutes (durée réglable dans Paramètres), sauf la pression
atmosphérique et la température extérieure, presque constantes. Un clic sur une tuile ouvre sa courbe détaillée :
1 min, 5 min, 15 min ou trajet entier, mise à jour en direct sur le trajet en cours, et une liste pour revoir les
trajets passés enregistrés dans la base. Le curseur donne la valeur et l'heure exactes ; les seuils d'alerte de la
métrique sont tracés quand ils tombent dans l'échelle. La courbe est coupée là où le calculateur n'a pas répondu.
Échap ou un clic à côté pour fermer.

La tuile **Conso. instantanée** (L/100 km, étiquette « calculé ») vaut débit de carburant ÷ vitesse × 100, comme
l'ordinateur de bord : elle est enregistrée avec les autres mesures (sa courbe se revoit sur les trajets passés),
plafonnée à 99 et vide sous 5 km/h.

## Coût des trajets

Dans les coins du compte-tours : à gauche la conso instantanée et la moyenne du trajet en cours, à droite le
**coût du trajet**, qui monte en direct (carburant consommé × prix au litre), avec les litres et les kilomètres.
Le carburant consommé est le débit (L/h) intégré sur le trajet : OBD standard, ou la mesure Renault `22 2806`
une fois validée dans l'onglet **Étalonnage** (sans elle, ni conso ni coût).

Le prix au litre se règle dans **Paramètres → Coût des trajets** (gazole, au prix du dernier plein). Il est
gardé par le script dans `data/reglages.json`. Chaque trajet garde le prix réglé pendant qu'il roulait : changer
le prix au plein suivant ne touche pas les trajets finis. Les trajets d'avant le premier prix réglé prennent
celui-ci. L'onglet **Trajets** donne pour chacun le coût, les litres, la moyenne en L/100 km et le prix retenu.

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
réponse, VIN) et une note libre saisie
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
d'admission, la conso instantanée celui du débit de carburant. Écriture groupée une fois par seconde, environ 3 Mo par heure de route.

| Table | Contenu |
|---|---|
| `sessions` | brut : `id` (UUID), `start_ms`, `end_ms`, `source` (adaptateur), `vin`, `version` (commit git), `metrics` |
| `samples` | brut : `session_id`, `t_ms` (ms depuis 1970, UTC), `key` (`rpm`, `coolant`… comme `METRICS`, plus `boost` et `l100`), `value` |
| `trips` | calculé : un trajet, sa fiche et sa note (voir plus haut) |
| `events` | calculé : `t_ms`, `kind` (`demarrage`, `arret_moteur`, `alerte`), `key`, `value`, `detail` |

Une base à l'ancien format (un trajet par lancement) est convertie toute seule au premier lancement. Une base
d'avant le coût des trajets reçoit ses deux colonnes (`fuel_l`, `fuel_price`) et ses trajets sont recalculés
(identifiants et notes gardés).
L'heure est celle de l'ordi : il doit être à l'heure. La version enregistrée est le commit git du script
(`CLIO_VERSION` dans l'environnement, sans dossier `.git`).

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
- La température d'huile et la tension batterie sont rarement dispo en OBD standard chez Renault : elles sont lues
  en UDS si le calculateur les donne (voir « Utilisation »). Le reste des mesures constructeur : repo de scan du
  calculateur.

## Serveur

[clio-sentinel-server](https://github.com/Aerandir1/clio-sentinel-server) tourne à la maison et garde
l'historique de tous les trajets. On donne son adresse au dashboard ; avec plusieurs adresses (séparées par des
virgules), la première qui répond est prise : la maison en direct sur le Wi‑Fi, sinon le VPN WireGuard sur la route.

```bash
python src/dashboard_clio.py --server http://192.168.1.13:8207,http://10.100.0.1:8207
```

Toutes les 60 s, le dashboard envoie au serveur les mesures qu'il n'a pas encore, par paquets, puis les trajets
s'ils ont changé. Entre deux, il vérifie toutes les 3 s que le serveur répond ; hors réseau, il réessaie sans
bruit et envoie dès que le serveur revient. Un envoi coupé est refait au suivant.
Un témoin en haut du dashboard le montre : vert « serveur · adresse » quand il répond (avec l'adresse prise : Wi‑Fi
ou VPN), rouge « serveur injoignable » sinon (la raison au survol), gris pendant le premier essai. Chaque
vérification est une présence (`POST /api/hello`) : la page du serveur affiche ce PC, son adresse et l'état de
l'ELM327. La base locale
n'est jamais modifiée. HTTP simple, sans jeton : le serveur n'est joignable que depuis la maison et le VPN.

| `clio.conf` | Option | Rôle |
|---|---|---|
| `SERVEUR` | `--server` | adresse(s) du serveur, séparées par des virgules |
| `NOM_PC` | `--server-name` | nom de ce PC sur la page du serveur (défaut : nom de la machine) |

- Service : `SERVEUR=192.168.1.13,10.100.0.1` dans `clio.conf` (port 8207 par défaut ; `http://IP:PORT` aussi accepté), puis
  `./service.sh restart` (Windows : `service.ps1 restart`). Serveur sur la même machine : `http://127.0.0.1:8207`.
- Envoi à la main, sans le dashboard : `python src/synchro.py --server ...`.
