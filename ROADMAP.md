# Roadmap Clio Sentinel

But final : à partir de tout ce que la voiture peut dire (et de ce qu'on peut en déduire), donner **un statut
par organe** (OK / à surveiller / à prévoir / urgent) et **anticiper les pannes** avant qu'elles arrivent.

Véhicule cible : Clio IV Estate dCi 90, 2016, moteur K9K (diesel, FAP).

---

## Où on en est (v0)

- Lecture OBD standard (mode 01) via ELM327 : régime, vitesse, températures, pression d'admission, débit d'air,
  pression de rail, EGR, tension… (liste `METRICS` de `dashboard_clio.py`).
- Dashboard temps réel, enregistrement CSV, mode démo, Docker.
- Extras multimédia : accueil 3D, autoradio, navigation, Spotify, CarPlay.

Limite actuelle : l'OBD standard ne donne qu'une partie des infos utiles. Une bonne partie de ce qui suit
demande les **requêtes constructeur Renault (UDS, mode 22)**.

---

## Phase 1 : Fondations

Avant de diagnostiquer quoi que ce soit, il faut stocker proprement et dans la durée.

- [ ] **Base de données des trajets** (SQLite) à la place des CSV seuls : un trajet = début, fin, km, mesures.
- [ ] **Carnet d'entretien** : saisie des interventions (vidange, courroie, filtres, embrayage, pneus…) avec km et date.
- [ ] **Kilométrage** : relevé manuel au départ, puis cumul des trajets (et lecture compteur en UDS si possible).
- [ ] **Profil véhicule** : VIN (mode 09), version calculateur, référence moteur exacte.
- [ ] **Séparer les extras** : la 3D, l'autoradio, CarPlay et Spotify rangés dans un module à part (`extras/`),
      le cœur du projet devient le diagnostic.

## Phase 2 : Codes défaut

- [ ] Lecture des **DTC** (modes 03 / 07 / 0A : actifs, en attente, permanents) et effacement (mode 04).
- [ ] **Explications en français** des codes, avec les causes fréquentes sur K9K.
- [ ] **Historique** : quand un code apparaît, avec quelles conditions (freeze frame, mode 02).
- [ ] Statut des **moniteurs de préparation** (mode 01 PID 01) : catalyseur, EGR, FAP…

## Phase 3 : Accès constructeur (UDS)

Le plus gros chantier, et la clé pour aller plus loin que l'OBD générique.

- [ ] Requêtes **UDS mode 22** vers le calculateur moteur, en s'appuyant sur les définitions de **DDT4all**.
- [ ] Mesures visées (à valider une par une, selon le calculateur de la voiture) :
  - **Injecteurs** : corrections de débit par cylindre (IMA / corrections d'équilibrage).
  - **FAP** : masse de suies, pression différentielle, km depuis la dernière régénération, nombre de régénérations.
  - **Huile** : température, usure calculée par le calculateur si dispo.
  - **Turbo** : pression de suralimentation consigne / mesurée.
  - **EGR** : position consigne / mesurée.
  - **Pression de rail** consigne / mesurée.
- [ ] Autres calculateurs si accessibles : ABS (capteurs de roue), BCM, airbag (lecture seule).

## Phase 4 : Indicateurs par organe

Chaque organe reçoit un score à partir de règles simples d'abord, puis de tendances.

| Organe | Comment on le juge |
|---|---|
| **Courroie de distribution** | km et âge depuis le remplacement (carnet), comparés à l'intervalle Renault. |
| **Vidange / filtres** | km et âge depuis la dernière intervention ; usure huile si le calculateur la donne. |
| **Embrayage** | **patinage** : régime moteur comparé à la vitesse × rapport engagé. Un écart qui grandit = embrayage fatigué. |
| **Injecteurs** | dérive des corrections par cylindre, ralenti instable (variations de régime). |
| **FAP** | fréquence des régénérations, suies, régénérations interrompues (trajets courts). |
| **EGR** | écart consigne / mesure dans le temps. |
| **Turbo** | écart suralimentation consigne / mesurée, temps de montée en pression. |
| **Refroidissement** | temps de chauffe, température max, comportement du thermostat. |
| **Batterie / charge** | tension au repos, au démarrage (chute au lancement), en charge. |
| **Pression de rail** | écart consigne / mesure, temps de mise en pression au démarrage. |
| **Freins / pneus** | surtout via le carnet (pas de capteur direct en OBD). |

- [ ] Page **« Santé du véhicule »** : une carte par organe avec son statut, sa tendance et la prochaine échéance.
- [ ] **Alertes** : prévenir quand un indicateur sort de sa plage ou dérive.

## Phase 5 : Prédiction

- [ ] **Références** : apprendre le comportement « normal » de la voiture sur les premiers mois.
- [ ] **Détection de dérive** : comparer chaque trajet à cette référence (moyennes glissantes, écarts).
- [ ] **Estimation d'échéance** : extrapoler les tendances (« embrayage : patinage en hausse, à prévoir d'ici X km »).
- [ ] Plus tard, si assez de données : modèles statistiques ou d'apprentissage sur l'historique.

## Phase 6 : Confort d'usage

- [ ] **Rapport après trajet** : résumé, anomalies, conso.
- [ ] **Export** du bilan (PDF) pour le garagiste ou la revente.
- [ ] Fonctionnement sur Raspberry Pi dans la voiture, en continu.

---

## Questions ouvertes

- Quel calculateur moteur exact sur cette voiture (Continental, Delphi…) ? Ça détermine les requêtes UDS disponibles.
- L'ELM327 actuel tient-il les requêtes UDS (certains clones bon marché ne gèrent pas tout) ?
- Où tourne l'outil à terme : PC portable ponctuellement, ou boîtier permanent dans la voiture ?
