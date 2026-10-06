# Roadmap Clio Sentinel

Principe : **tout part de la donnée.** La voiture a des dizaines de capteurs ; chacun, seul, dit peu de chose.
Croisés entre eux et suivis dans le temps, ils disent dans quel état est chaque organe et comment il évolue.
But final : **un statut par organe** et **l'anticipation des pannes**.

Véhicule cible : Clio IV Estate dCi 90, 2016, moteur K9K (diesel, FAP).

---

## Phase 1 : Capter le maximum, proprement

Sans données fiables et nombreuses, rien de ce qui suit ne marche. C'est la priorité.

- [ ] **Inventaire des capteurs** : demander au calculateur tous les PID qu'il supporte (pas seulement ceux de
      `METRICS`) et les enregistrer tous.
- [ ] **Échantillonnage intelligent** : l'ELM327 ne fait que quelques mesures par seconde au total. On les répartit :
      rapides (régime, vitesse, pression d'admission, pédale) souvent ; lentes (températures, pression atmo) rarement.
- [ ] **Captures rapides sur événement** : au démarrage (chute de tension, montée en pression du rail),
      en accélération franche (montée du turbo), au ralenti (stabilité).
- [ ] **Stockage par trajet** (SQLite) : chaque mesure horodatée, plus le contexte du trajet
      (départ à froid ou à chaud, température extérieure, durée, part ville / route / autoroute, GPS si dispo).
- [ ] **Qualité des données** : repérer les trous, les valeurs aberrantes, les capteurs qui ne répondent plus.

## Phase 2 : Faire parler les capteurs (signaux calculés)

On combine les mesures brutes pour obtenir ce que la voiture ne donne pas directement.
Tout ça est faisable **avec l'OBD standard**, sans outil constructeur.

| Ce qu'on calcule | À partir de | Ce que ça surveille |
|---|---|---|
| **Rapport engagé** | régime ÷ vitesse (rapports appris automatiquement sur les données) | base pour le reste |
| **Patinage d'embrayage** | régime réel comparé au régime attendu pour le rapport engagé, sous forte charge | **embrayage** |
| **Stabilité du ralenti** | variations de régime au ralenti à chaud | **injecteurs**, supports moteur |
| **Suralimentation** | pression d'admission − pression atmo | **turbo** |
| **Temps de réponse turbo** | délai entre appui pédale et montée en pression | **turbo**, fuites d'admission |
| **Débit d'air attendu vs mesuré** | débit d'air comparé à celui prévu par régime, pression et température | **débitmètre**, fuites, **EGR** bloquée ouverte |
| **Efficacité de l'échangeur** | température d'admission comparée à la température extérieure | **intercooler** |
| **Courbe de chauffe** | montée de la température d'eau, palier atteint | **thermostat**, refroidissement |
| **Montée en pression du rail** | temps pour atteindre la pression au démarrage | **pompe HP**, injecteurs (fuites) |
| **Écart EGR** | EGR commandée comparée à l'écart mesuré | **vanne EGR** (encrassement) |
| **Santé batterie / charge** | tension au repos, chute au démarrage, tension en roulant | **batterie**, **alternateur** |
| **Détection des régénérations FAP** | signature : ralenti relevé, EGR fermée, conso en hausse | **FAP** (fréquence, régénérations interrompues) |
| **Richesse estimée** | débit carburant ÷ débit d'air | **injection**, fumées |
| **Freinages** | décélérations (dérivée de la vitesse) | usure **freins** (estimation) |
| **Style de conduite** | régimes, accélérations, trajets courts | facteur d'usure global |

## Phase 3 : Comparer à soi-même (cartographies et dérives)

Un chiffre isolé ne veut rien dire : 1,2 bar de turbo, c'est bien ou pas ? Ça dépend du régime et de la charge.

- [ ] **Cartographies de référence** : pour chaque signal, sa valeur « normale » par point de fonctionnement
      (grille régime × charge, moteur chaud). Apprises sur les premiers trajets.
- [ ] **Comparer uniquement le comparable** : même régime, même charge, même température.
- [ ] **Suivi des dérives** : chaque trajet est comparé à la référence ; on trace l'écart dans le temps.
- [ ] **Vue d'exploration** : courbes et nuages de points sur l'historique, pour voir ce que racontent les données.

## Phase 4 : Plus de capteurs (accès constructeur)

L'OBD standard est une petite fenêtre. Le calculateur en sait beaucoup plus.

- [ ] Requêtes **UDS mode 22** vers le calculateur moteur, à partir des définitions de **DDT4all**.
- [ ] Mesures visées, à valider une par une selon le calculateur de la voiture :
  - **corrections d'injecteurs par cylindre** : la mesure directe de leur santé ;
  - **FAP** : masse de suies, pression différentielle, km depuis la dernière régénération ;
  - **turbo** et **EGR** : consigne comparée à la mesure ;
  - **pression de rail** : consigne comparée à la mesure ;
  - **température d'huile**, usure huile calculée si dispo.
- [ ] Ces nouvelles mesures rejoignent les phases 2 et 3.

## Phase 5 : Statut par organe

- [ ] Un **score par organe** (OK / à surveiller / à prévoir / urgent) à partir de ses signaux et de leur dérive.
- [ ] Page **« Santé du véhicule »** : une carte par organe, avec son statut, sa tendance et les mesures qui l'expliquent.
- [ ] **Alertes** quand un signal sort de sa plage ou dérive trop vite.

## Phase 6 : Prédiction

- [ ] **Extrapoler les dérives** : « patinage d'embrayage en hausse, à prévoir d'ici X km ».
- [ ] **Détection d'anomalies** sur l'ensemble des signaux (ce qui ne ressemble pas aux trajets habituels).
- [ ] Avec assez d'historique : modèles statistiques ou d'apprentissage.

## Plus tard : le reste

- Codes défaut (lecture, explications, historique), à brancher sur les statuts.
- Carnet d'entretien et kilométrage : pour la courroie de distribution, les filtres et la vidange,
  qui se suivent au km et à l'âge plus qu'aux capteurs.
- VIN, profil véhicule.
- Rapport après trajet, export PDF.
- Boîtier permanent (Raspberry Pi) pour enregistrer tous les trajets sans y penser.
- Extras multimédia (accueil 3D, autoradio, CarPlay, Spotify) rangés dans un module à part.

---

## Questions ouvertes

- Combien de PID le calculateur supporte-t-il vraiment ? (premier test à faire, voiture contact mis)
- Quelle cadence réelle tient l'ELM327 ? Ça décide de ce qu'on peut capter finement.
- Quel calculateur moteur exact (Continental, Delphi…) ? Ça décide des mesures UDS disponibles.
- L'ELM327 actuel tient-il les requêtes UDS ?
