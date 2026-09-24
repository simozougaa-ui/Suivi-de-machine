# Notes de session — mise en place du projet

## État actuel (mise à jour du 2026-09-24)

Le contexte ci-dessous (« Contexte de cette session ») date de la toute
première session, **avant réception du matériel** : il mentionne un DVR
Hikvision et un Jetson non reçu. C'est obsolète, gardé seulement comme
trace historique des choix initiaux. La réalité aujourd'hui :

- Le matériel est reçu et en service : **Jetson Orin Nano Super**, Ubuntu
  24.04, accessible à distance via **Tailscale** (VPN privé — l'appareil
  garde son adresse même en 4G, pas besoin d'être sur le wifi de
  l'atelier).
- Le DVR est un **Dahua**, pas un Hikvision — voir `src/camera_stream.py`
  (`build_rtsp_url`, `build_rtsp_playback_url`) pour le format d'URL
  correct (`/cam/realmonitor` en direct, `/cam/playback` pour les
  enregistrements, tous deux spécifiques Dahua).
- `CAMERA_CHANNEL` est le numéro de canal **tel qu'affiché dans l'appli
  DMSS** (ex: 15), pas un numéro de flux Hikvision.
- Deux services systemd tournent en continu sur le Jetson (voir
  `deploy/`) : `suivi-presence` (lance `main.py`, écrit `sessions.csv`) et
  `suivi-dashboard` (sert `dashboard.py` sur le port 8000, accessible par
  Tailscale depuis un téléphone).
- Résolution du flux principal (caméra 15, canal DMSS 15) : **1280x720**.

### Historique de calibrage de la zone de présence

Plusieurs approches ont été essayées, dans cet ordre, chacune remplacée
parce qu'elle ratait trop de cas réels :

1. **`WORK_ZONE` + YOLOv8n (détection de personne entière)** — zone
   rectangulaire, recalibrée plusieurs fois (`main.py`, `WORK_ZONE`).
   Problème : la caméra voit le conducteur de loin et de dos, avec le
   corps souvent coupé par la structure de la machine — YOLO, entraîné à
   reconnaître des personnes entières, rate ces cas très souvent.
2. **Soustraction de fond sur tout le contour de la machine**
   (`src/background_detection.py`, `MACHINE_POLYGON`) — compare l'image
   entière à une référence "machine vide". Plus robuste au corps coupé,
   mais trop large : capte aussi le mouvement de la machine elle-même
   quand elle tourne (rouleaux, mécanismes), donnant de fausses présences
   continues si la référence est prise machine à l'arrêt. Gardé dans le
   dépôt (mode "yolo" et le code de `background_detection` restent
   disponibles) mais plus utilisé par défaut.
3. **Détection par fragments dans 3 petites zones** (ci-dessous) —
   méthode retenue par défaut depuis cette session.

### Détection par fragments (méthode actuelle, `PRESENCE_MODE=fragments`)

**Constat de départ.** Sur plusieurs captures de la caméra 15, le
conducteur (t-shirt noir) n'est jamais visible en entier : la poutre
horizontale perforée et les montants de la machine du milieu (le même
contour que `MACHINE_POLYGON`, x≈180-700 / y≈90-410 sur le flux) lui
coupent le corps. Mais il reste presque toujours visible **par morceaux**,
dans l'une de 3 zones autour de cette machine :

- **ZONE_TETE** (x 580-700, y 85-160) : juste au-dessus du bord supérieur
  de la machine, près de la boucle de câble et de la roue — masse sombre
  arrondie (tête/casquette).
- **ZONE_JAMBES** (x 520-640, y 210-340) : sous la poutre horizontale
  perforée, entre le montant pâle et le montant sombre — pantalon sombre
  et chaussures visibles à travers les barreaux.
- **ZONE_PILE** (x 630-700, y 220-400) : autour des piles de feuilles à
  droite du montant sombre — dos/bras penché dessus, ou feuille portée.

Voir `src/fragment_detection.py` pour le code et les commentaires détaillés.

**⚠️ Limite connue et honnête sur ce calibrage.** Les coordonnées
ci-dessus ont été fixées en repérant les éléments fixes de la machine
(boucle de câble, roue, poutre perforée, montants) sur une image de
référence déjà capturée plus tôt dans le projet
(`calibrate_day.png` du 2026-09-05 12:00:20), **sans accès réseau direct
au DVR** depuis l'environnement où ce code a été écrit (contrairement au
Jetson, qui lui est sur le même réseau que le DVR). Ce n'est donc **pas**
un calibrage validé image par image sur des captures fraîches — c'est une
première estimation raisonnée, à confirmer avec les outils prévus pour
ça :

1. `compute_reference_fragments.py --date AAAA-MM-JJ --debut HH:MM:SS --duree 3600 --pas 30`
   pour recalculer `reference_fragments.png` (médiane d'images espacées,
   efface les présences occasionnelles et ne garde que le fond).
2. `contact_sheet.py --date AAAA-MM-JJ --debut HH:MM:SS --duree 1200`
   pour produire une planche de vignettes (une toutes les 10s, ~20 min)
   avec le statut prédit et les 3 zones dessinées (rouge = déclenchée) —
   à regarder soi-même pour vérifier visuellement que ça correspond à la
   réalité.
3. `test_fragments_on_recording.py --date AAAA-MM-JJ --debut HH:MM:SS --duree 1800`
   pour une chronologie texte présent/absent seconde par seconde, avec le
   ratio de chaque zone — utile pour ajuster `THRESHOLDS` dans
   `src/fragment_detection.py` zone par zone.

**Taux de bonnes détections observé : pas encore mesuré** — cette
validation (étape 2/3 ci-dessus, sur au moins 20 minutes d'enregistrement
réel) n'a pas pu être faite dans cette session faute d'accès direct au
flux DVR. C'est la prochaine étape à faire depuis le Jetson (ou une
session future y ayant accès) avant de faire confiance aux données de
`sessions.csv` produites en mode fragments.

**Cas d'échec probables à surveiller lors de la validation :**
- Éclairage jour/nuit (mode infrarouge de la caméra la nuit) : la
  référence doit être recalculée séparément si la surveillance doit aussi
  couvrir des heures sombres — la normalisation gain/offset ne corrige
  que des écarts d'exposition modérés, pas un changement de mode
  caméra (couleur → infrarouge).
- Un autre ouvrier (t-shirt bleu) travaille sur une machine différente en
  haut à gauche de l'image, hors des 3 zones — à vérifier qu'il ne
  déclenche jamais une fausse présence si une zone est mal placée trop
  large.
- Des personnes passant près des palettes au premier plan (bas gauche de
  l'image) sont hors zone — même remarque.
- Si `ZONE_JAMBES` ou `ZONE_PILE` s'avèrent trop décalées lors de la
  validation, revoir avec `calibrate_zone_daytime.py` (grille de
  coordonnées) sur une nouvelle capture pendant que quelqu'un travaille
  réellement dans chaque zone.

**Intégration dans `main.py`.** Variable d'environnement
`PRESENCE_MODE` : `fragments` (défaut) | `yolo` | `both` (l'un OU
l'autre déclenche une présence). Le YOLO et la soustraction de fond sur
toute la machine restent dans le dépôt, non supprimés, pour comparaison
ou secours. `main.py` continue de lire chaque image du flux (pour éviter
le retard d'accumulation RTSP) mais n'analyse qu'environ 1 image par
seconde. Toutes les 10s, `main.py` écrit `zones_debug.png` (dernière
image avec les 3 zones dessinées et leur ratio), servi par
`dashboard.py` — consultable depuis le téléphone via Tailscale sans SSH.
`ABSENCE_TOLERANCE_SECONDS` (défaut 30s, avant 8s) a été augmenté : les
allers-retours du conducteur pour chercher une palette, ou un simple
masquage par la machine, durent souvent 6 à 20s — 8s coupait des
sessions réelles en plusieurs morceaux.

## Contexte de cette session

*(Section historique — voir « État actuel » ci-dessus pour la situation réelle.)*

Le matériel cible (Jetson Orin Nano Super) n'était **pas encore reçu** au
moment de cette session, et l'environnement de développement n'a **aucun
accès réseau direct au DVR Hikvision**. Le code a donc été écrit "à
l'aveugle", sans possibilité de test en conditions réelles. Cette section
documente les choix faits et ce qu'il reste à valider à réception du
matériel.

## Choix techniques

### Modèle de détection : YOLOv8n

- **YOLOv8n** ("nano") est le plus petit modèle de la famille YOLOv8
  d'Ultralytics : le meilleur compromis vitesse/légèreté pour un boîtier
  embarqué comme le Jetson Orin Nano Super.
- Pré-entraîné sur COCO, qui inclut nativement la classe `person`
  (`class_id = 0`) — pas besoin d'entraînement personnalisé pour cette
  première étape.
- Gratuit et open-source (licence AGPL-3.0 via `ultralytics`), pas de coût
  d'API ni de dépendance à un service cloud.
- Alternative envisagée : utiliser directement TensorRT / DeepStream
  (spécifique NVIDIA) pour de meilleures performances sur Jetson — écarté
  pour cette étape afin de garder un code Python simple et portable, à
  optimiser plus tard une fois les performances réelles mesurées sur le
  matériel.

### Structure des fichiers (`src/`)

Découpage en 4 modules à responsabilité unique, pour pouvoir tester et
remplacer chaque brique indépendamment :

- `camera_stream.py` — uniquement la connexion RTSP et la lecture d'images
  (générateur `frames()` avec reconnexion automatique en cas de coupure
  réseau, car le boîtier doit tourner sans surveillance).
- `detection.py` — uniquement le chargement du modèle et l'inférence
  (fonction `detect_persons()` réutilisable indépendamment de la source
  d'image, utile pour tester avec des images statiques).
- `zone.py` — uniquement la géométrie (classe `Zone`, calcul d'intersection
  par ratio de recouvrement plutôt que simple point-dans-rectangle, pour
  éviter les faux positifs d'une personne qui ne fait que longer la zone).
- `main.py` — assemble les trois briques dans une boucle simple, affiche le
  résultat en console. Pas d'envoi API pour l'instant (voir ci-dessous).

### Gestion des identifiants

- Aucune valeur réelle (IP, utilisateur, mot de passe) n'est présente dans
  le code : tout passe par des variables d'environnement chargées via
  `python-dotenv`, avec `.env` dans `.gitignore` et `.env.example` comme
  gabarit versionné.
- `camera_stream.build_rtsp_url()` échoue explicitement (message clair) si
  une variable requise est absente, plutôt que de laisser `cv2` échouer
  silencieusement avec une URL invalide.

### Robustesse (code non testable en réel)

- `camera_stream.frames()` boucle indéfiniment et se reconnecte
  automatiquement (délai de 5s) en cas d'échec d'ouverture du flux ou
  d'erreur de lecture — le boîtier doit pouvoir survivre à une coupure
  réseau ou un redémarrage du DVR sans intervention humaine.
- `main.py` capture les exceptions autour de la détection par image (une
  image en erreur ne doit pas arrêter la boucle).
- Les URLs loguées ne contiennent jamais le mot de passe (`_safe_url_for_logs`).

## Ce qui reste à faire à réception du matériel

1. **Test de connexion RTSP réel** : vérifier que
   `rtsp://<user>:<password>@192.168.0.130:554/Streaming/Channels/1501`
   s'ouvre bien avec `cv2.VideoCapture` depuis le Jetson (le flux DVR
   Hikvision peut nécessiter un transport RTSP spécifique — tester TCP vs
   UDP si l'image est instable ou ne s'ouvre pas).
2. **Installation sur le Jetson** : JetPack, drivers GPU NVIDIA, et
   vérifier que `ultralytics`/`opencv-python` s'installent correctement
   dans cet environnement (envisager `opencv-python-headless` si pas
   d'affichage, ou les builds optimisés NVIDIA si les perfs CPU sont
   insuffisantes).
3. **Calibrage de la zone de travail** : la `WORK_ZONE` codée en dur dans
   `main.py` (`x1=200, y1=150, x2=600, y2=450`) est un **placeholder**. Il
   faudra capturer une image réelle du flux, l'inspecter (par ex. en
   sauvegardant une frame en PNG) pour déterminer la résolution réelle et
   les coordonnées correspondant à la zone de travail physique.
4. **Ajout de l'envoi vers l'API `suivi-production-imprimerie`** : le
   `TODO` dans `main.py` est prêt à recevoir un appel `requests.post(...)`
   vers l'API une fois l'endpoint et le format d'échange définis côté
   `suivi-production-imprimerie`. Prévoir gestion d'erreurs réseau (timeout,
   API indisponible) pour ne pas bloquer la boucle de détection.
5. **Mesure de performance réelle** sur le Jetson (FPS, latence) pour
   éventuellement ajuster la fréquence d'inférence (ne pas forcément
   traiter chaque frame du flux).
