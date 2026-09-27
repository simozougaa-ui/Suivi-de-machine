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
   pour produire des pages de vignettes (une image toutes les 10s, ~20 min)
   avec le statut prédit et les 3 zones dessinées (rouge = déclenchée) —
   à regarder soi-même (ou via `/contact` sur le tableau de bord) pour
   vérifier visuellement que ça correspond à la réalité. Voir la section
   dédiée ci-dessous pour le détail (recadrage, pagination, stockage des
   images source).
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

### Feuille de contact lisible sur téléphone (mise à jour du 2026-09-24)

**Problème signalé** : `contact_sheet.png` (première version) produisait
une seule grande image de 60 vignettes montrant toute la caméra en
320px de large — illisible sur téléphone, le conducteur n'y faisait
qu'environ 15px de haut.

**Corrections apportées à `contact_sheet.py` :**

- **Recadrage** sur une constante `CROP = (420, 60, 820, 440)` (pixels du
  flux 1280x720), qui couvre les 3 zones (tête/jambes/pile) et le bout
  droit de la machine — vérifié visuellement (image générée et regardée)
  que les 3 zones tiennent entièrement dedans, sans coupure.
- Les vignettes ne sont **jamais réduites**, seulement agrandies
  (`cv2.INTER_CUBIC`) pour que 2 côte à côte fassent ~1080px de large
  (`THUMB_WIDTH = 540`).
- **Étiquettes de zone à l'intérieur du rectangle**, pas au-dessus : un
  premier essai plaçait le texte juste au-dessus de chaque zone, mais
  `ZONE_JAMBES` et `ZONE_PILE` se touchent presque (x 520-640 et
  630-700) — leurs étiquettes se chevauchaient et devenaient illisibles.
  Corrigé en écrivant le texte (`nom ratio/seuil`, ex. `tete 0.12/0.08`)
  dans le coin haut-gauche de chaque rectangle, dans sa propre couleur —
  vérifié sur une image générée, plus de chevauchement.
- **Pagination** : 6 vignettes par page (2 colonnes x 3 lignes), dans
  `contact_pages/page_01.png`, `page_02.png`, etc. Les anciennes pages du
  dossier sont supprimées avant d'écrire les nouvelles (évite de garder
  d'anciennes pages en trop si le nombre de vignettes diminue d'une
  exécution à l'autre).
- **Images source conservées** : chaque image plein cadre échantillonnée
  est sauvegardée en JPEG qualité 92 dans
  `debug_frames/<AAAA-MM-JJ>_<HHMMSS-debut>/frame_<HHMMSS>.jpg` — le DVR
  Dahua écrase ses enregistrements au bout de 17 jours, donc ces copies
  sont la seule trace durable si on veut revalider plus tard.
- **`--depuis-dossier CHEMIN`** : régénère les pages à partir d'un dossier
  `debug_frames/...` déjà rempli, sans reconnexion au DVR (la date est
  déduite du nom du dossier, l'heure de chaque image de son nom de
  fichier).
- **`--pas`** (secondes entre deux images échantillonnées, défaut 10)
  ajouté en plus de `--date --debut --duree`.
- `debug_frames/` et `contact_pages/` ajoutés au `.gitignore` — ce sont
  des images régénérables à la demande, pas du code.

**`dashboard.py` — page `/contact`** : liste les pages disponibles avec
navigation précédent/suivant (`/contact?page=N`), image en largeur 100%
(balise viewport), lisible sur téléphone. Sert aussi
`/contact_pages/page_NN.png` via une expression régulière **stricte**
(`^/contact_pages/page_\d{2}\.png$`) qui n'autorise que ce motif exact —
testé explicitement contre plusieurs tentatives de traversée de chemin
(`/contact_pages/../main.py`, `/contact_pages/page_01.png/../../.env`) et
contre des motifs proches mais invalides (`page_1.png`, `page_01.jpg`) :
tous tombent correctement en dehors de la route et n'exposent aucun
fichier. Les routes existantes (`/`, `/calibrate.png`, etc.) sont
inchangées.

**Journal en direct sous `nohup`** : `flush=True` ajouté à tous les
`print()` de `contact_sheet.py`, `compute_reference_fragments.py` et
`test_fragments_on_recording.py`, pour que `tail -f contact.log` montre
la progression immédiatement plutôt que par paquets bufferisés.

**Validation faite dans cette session** : avec des images synthétiques
(bruit aléatoire, pas d'enregistrement réel — même contrainte d'accès
réseau que pour le calibrage initial des zones, voir plus haut) :
contour des 3 zones vérifié à l'intérieur de `CROP`, rendu d'une
vignette et d'une page complète regardés directement (chevauchement
d'étiquettes détecté et corrigé grâce à ça), pagination (nettoyage des
anciennes pages, découpage en pages de 6) vérifiée par du code qui crée
un fichier factice `page_99.png` puis vérifie qu'il disparaît. Les
routes `/contact` et `/contact_pages/page_NN.png` ont été testées avec
un vrai serveur HTTP local (module `http.server`), y compris les
tentatives de traversée de chemin ci-dessus. **Reste à faire depuis le
Jetson** : générer une vraie feuille de contact sur un enregistrement
réel (par ex. le 2026-09-23 vers 13:21) et vérifier que le recadrage
`CROP` montre bien le conducteur quand il est présent — pas seulement
que les zones y tiennent géométriquement.

### Zone `convoyeur` ajoutée (mise à jour du 2026-09-24)

**Problème signalé** : les zones rataient le conducteur quand il manipule
la palette de feuilles sur le côté droit de la machine, le long du
convoyeur de sortie (confirmé visuellement sur l'enregistrement du
2026-09-23, 13:21:07–13:21:16 : conducteur juste à droite de la tête de
machine, hors des rectangles).

**Anciennes zones (inchangées)** : tete (580,85,700,160), jambes
(520,210,640,340), pile (630,220,700,400).
**Nouvelle zone** : `convoyeur` = **(700, 150, 790, 250)**, seuil 0.06.

**Pourquoi une 4e zone plutôt qu'agrandir `pile`** : le détecteur mesure
une *fraction* de pixels changés ; agrandir une zone dilue ce ratio et la
rend moins sensible à un fragment de corps.

**Calage.** Le DVR n'est pas joignable depuis l'environnement de
développement (pas sur le réseau Tailscale) : impossible de récupérer la
frame du 2026-09-23 13:21:16. La zone a été calée sur la vue plein cadre
de la même caméra fixe, reconstituée à partir des captures DMSS du
2026-09-05 (zone vidéo extraite et remise à 1280x720) : montant droit de
la machine x≈700, plateau du convoyeur x 720–790 / y 185–225, palette
blanche x 715–805 / y 240–300, poteau vertical x≈790, machine voisine
au-delà de x≈800 (exclue), presse bleue sous y≈340 (exclue).

**Itération sur de vraies images.** 86 captures réelles de la caméra 15
(2026-09-05, 11:48–12:00, période où le conducteur travaillait sur cette
machine), référence = médiane de ces images (même méthode que
`compute_reference_fragments.py`) :

| Borne basse y2 | Déclenchements convoyeur | Palette modifiée, personne absente | Conducteur portant des feuilles |
|---|---|---|---|
| 300 (1er essai, inclut le dessus de la palette) | 52/86 | 0.093 → **faux positif** | 0.232 |
| 250 (**retenu**) | 15/86 | 0.001 | 0.25 |
| 240 | 11/86 | 0.001 | 0.21 |

- Premier essai y2=300 : la zone incluait le dessus de la palette. Dès que
  le conducteur retire des feuilles, le niveau de la palette change
  **durablement** par rapport à la référence → faux positif permanent
  jusqu'au prochain recalcul de référence. Vu directement sur les images
  (palette plus basse, aucune personne dans le cadre).
- y2=250 : les 15 images qui déclenchent ont toutes été regardées une par
  une — le conducteur y est visible à chaque fois (portant une pile de
  feuilles au-dessus du convoyeur, penché vers la palette, bras tendus).
  Aucun faux positif constaté. y2=240 en perdait 4 (conducteur penché
  bas, juste au-dessus de la palette).
- Présence brute (au moins une zone) sur ces 86 images : **29 avec les 3
  anciennes zones → 40 avec `convoyeur`**, dont 11 vues uniquement par la
  nouvelle zone.

**Limite restante connue** : si le conducteur se tient *derrière* la
palette sans rien dépasser au-dessus (y > 250), la zone ne le voit pas —
c'est le prix à payer pour éviter la dérive due au niveau de la palette.

**`contact_sheet.py`** : `CROP` élargi de (420,60,820,440) à
(420,60,860,440) pour que la palette et la nouvelle zone soient
entièrement visibles sur les vignettes (vérifié sur des vignettes
générées à partir des vraies captures).

**Reste à faire depuis le Jetson** (DVR inaccessible d'ici) : rejouer la
plage du 2026-09-23 13:15–13:30 et vérifier la présence continue sur
13:21:07–13:21:16 (commandes dans README.md, section « Vérifier la zone
convoyeur »). Pour la référence, préférer une fenêtre plus longue que les
15 minutes testées (ex. `--debut 12:30:00 --duree 5400 --pas 30`) : si le
conducteur reste plus de la moitié du temps au même endroit dans une
fenêtre courte, la médiane l'intègre dans la référence et le rend
invisible.

### Durée minimum sur la zone convoyeur (mise à jour du 2026-09-26)

**Problème** : un chemin de passage traverse la zone convoyeur
(personnes, palettes sans lien avec la machine) → faux positifs sur de
brefs passages.

**Règle** : un changement dans la zone convoyeur ne compte comme présence
que s'il persiste au moins `CONVOYEUR_DUREE_MIN_SEC = 4` secondes
(constante en haut de `src/fragment_detection.py`). Tête, jambes et pile
sont inchangées. Pendant ces 4 s, la zone est affichée en **orange** sur
les vignettes de `contact_sheet.py` (seuil dépassé, durée pas encore
atteinte) — un passage filtré se repère donc visuellement.

**Implémentation choisie** :
- Durée mesurée en **temps réel** entre la première et la dernière mesure
  déclenchées consécutives (pas en nombre d'images), dans
  `FragmentPresenceTracker`. Le tracker reçoit l'heure de chaque mesure :
  heure de la vidéo en relecture (`contact_sheet.py`,
  `test_fragments_on_recording.py`), horloge système en direct
  (`main.py`, inchangé). Une seule mesure ne suffit jamais (durée nulle).
- Le filtre s'applique **avant** le lissage existant (2 mesures sur 3) :
  à 1 mesure/s, une présence est donc confirmée ~5 s après son début.
  Conséquence : les sessions de `sessions.csv` commencent ~5 s après
  l'arrivée réelle quand seule la zone convoyeur le voit (négligeable
  devant la tolérance d'absence de 30 s).
- **`contact_sheet.py` découplé** : la détection tourne à 1 image/s
  (comme `main.py`) quel que soit `--pas`, qui ne règle plus que
  l'intervalle entre vignettes. Sinon, avec `--pas 10`, une image toutes
  les 10 s ne permet pas de distinguer un passage de 2 s d'une présence
  de 9 s, et le filtre ne pourrait pas s'appliquer de façon cohérente.
- **Limite `--depuis-dossier`** : seules les images sauvegardées (une par
  `--pas`) existent, la détection ne peut donc tourner que sur elles.
  Mesuré : avec des images toutes les 5 s, la présence de 10 s
  (13:21:07–16) est **perdue** ; toutes les 2 s, elle est retrouvée. Le
  script affiche un avertissement quand les images sont espacées de plus
  de 2 s. Les pages produites en lecture directe du DVR font foi.
  (Sauvegarder une image par seconde coûterait ~200 Mo par 20 min.)

**Test** (DVR toujours injoignable depuis l'environnement de dev, donc
pas l'enregistrement du 2026-09-23) : séquence reconstituée à partir de
deux **vraies** captures de la caméra 15 — couloir vide, et conducteur
dans la zone convoyeur (ratio 0.21, autres zones au repos) — rejouée à
15 images/s à travers le vrai code de `contact_sheet.py` (seule
l'ouverture du flux RTSP est simulée), heures calées sur la plage du
2026-09-23 :

| Événement | Sans filtre | Avec filtre 4 s (`--pas 10` et `--pas 5`) |
|---|---|---|
| Passage 3 s (13:20:50–52) | présent 13:20:51–53 (**faux positif**) | ignoré |
| Conducteur 13:21:07–16 | présent 13:21:08–17 | **présent 13:21:12–17** |
| Passage 2 s (13:21:25–26) | présent 13:21:26–27 (**faux positif**) | ignoré |
| Reste de la minute (vide) | absent | absent |

**Reste à faire sur le terrain** : aucun enregistrement avec des passages
réels connus (< 4 s) dans la zone convoyeur n'était disponible ici. À
vérifier depuis le Jetson : `test_fragments_on_recording.py --date
2026-09-23 --debut 13:21:00 --duree 1440` (chronologie par seconde : `*` = zone
comptée, `~` = seuil dépassé mais durée minimum pas encore atteinte ; un
passage filtré apparaît en `convoyeur=0.xx~` sans `PRESENT`), et `contact_sheet.py` sur la même plage pour repérer les
vignettes orange. Si de vrais passages durent plus de 4 s (palette
poussée lentement), augmenter `CONVOYEUR_DUREE_MIN_SEC`.

### Évaluation YOLO isolée (mise à jour du 2026-09-26)

But : savoir si un vrai détecteur de personne peut remplacer ou compléter
la détection par fragments. Tout est dans `yolo_eval/` (voir son README) ;
**aucun fichier de production n'a été modifié**.

**Contrainte** : l'environnement de développement n'a accès ni au Jetson
ni au DVR (Tailscale injoignable). L'enregistrement du 2026-09-23 et les
FPS du Jetson n'ont donc **pas** pu être mesurés ici : le kit fournit les
commandes à lancer sur le Jetson. La qualité de détection a été mesurée
sur **57 vraies images distinctes de la caméra 15** (2026-09-05
11:48–12:00, 1280x720), dont **15 avec le conducteur au convoyeur**,
vérifiées à l'œil.

**Choix (décidés sans validation, justifiés par les mesures ci-dessous)**

- **Modèle YOLO11n** : même coût que YOLOv8n, un peu meilleur ; 11s/11m
  gagnent 1 à 4 détections sur 57 pour 2 à 6 fois plus de calcul.
- **imgsz 1280** : les personnes sont petites dans cette vue ; à 640,
  l'ouvrier du fond est vu 0 à 4 fois sur 57, à 1280 43 fois.
- **conf 0.30** : élimine la seule boîte douteuse (tache sombre de
  l'escalier, 0.25–0.29) sans perdre de vraies personnes.
- **Format** : PyTorch pour l'évaluation, plus un moteur **TensorRT FP16**
  construit avec `trtexec` de JetPack (le `torch` de PyPI ne voit pas le GPU
  Orin, donc ultralytics tourne sur CPU sur le Jetson ; trtexec mesure le
  débit GPU réel sans dépendre de torch).
- **venv isolé** `yolo_eval/.venv` (`--system-site-packages` pour voir le
  module tensorrt de JetPack), `ultralytics==8.4.162`.

**Qualité mesurée (57 images réelles)**

| Modèle / imgsz | Conducteur au convoyeur (15) | Ouvrier du fond vu (57) | Temps CPU dev / image |
|---|---|---|---|
| YOLOv8n 640 | 0 (1 à conf 0.10) | 0 | 45 ms |
| YOLO11n 640 | 0 | 4 | 43 ms |
| YOLOv8n 1280 | 0 | 43 | 104 ms |
| **YOLO11n 1280** | **0** | **43** | **101 ms** |
| YOLO11s 1280 | 0 | 44 | 227 ms |
| YOLO11m 1280 | 0 | 47 | 621 ms |

- **Conducteur au convoyeur : 0/15, quel que soit le modèle** (aucune
  boîte, même à conf 0.10). Il est penché, vu de haut, coupé par la
  poutre et le poteau, souvent caché par les feuilles qu'il porte : ce
  n'est pas une silhouette de personne pour un modèle COCO générique.
  C'est exactement le cas que la détection par fragments rattrape.
- **Personnes debout bien visibles : détectées** — ouvrier en bleu au fond
  (0.36–0.71), ouvrier à droite (0.28–0.40), passant au premier plan (0.56),
  personne debout derrière la tête de la machine (0.36–0.42, seul cas
  détecté dans la zone machine).
- **Faux positifs** : aucune partie de la machine prise pour une personne ;
  une seule boîte douteuse (escalier, < 0.30, éliminée par le seuil).
- Test bout en bout de `eval_yolo.py` (20 images, CPU de dev) : 108 ms/image
  soit **9,3 FPS sur ce CPU** ; 13:21:07 (conducteur présent) = non détecté.
- Images : `yolo_eval/resultats_2026-09-26/` (15 vignettes conducteur sans
  aucune boîte, planche des boîtes trouvées, deux exemples annotés).

**FPS sur le Jetson : à mesurer** (commandes ci-dessous). Estimation : CPU
ARM du Jetson plus lent que celui de dev, donc probablement 2–5 FPS en
PyTorch CPU à 1280, et nettement plus en TensorRT FP16 sur GPU. Attention :
sur CPU, YOLO concurrence le service `suivi-presence` ; ~1,5 Go de RAM en
plus pendant le test.

**Recommandation**

1. **Ne pas remplacer** la détection par fragments par un YOLO générique :
   il rate systématiquement le conducteur dans la position qui compte.
2. YOLO peut servir en **complément** pour les personnes debout (passants,
   autres ouvriers), par exemple pour confirmer/écarter une présence ; pas
   prioritaire.
3. Pour que YOLO voie vraiment le conducteur : **entraîner un petit YOLO
   (11n) sur des images de cette caméra** annotées à la main
   (quelques centaines d'images tirées de `debug_frames/`), puis l'exporter
   en TensorRT ; ou améliorer l'angle de caméra.

**Commandes à lancer sur le Jetson**

```bash
cd ~/suivi-de-machine && git pull
bash yolo_eval/check_env.sh | tee yolo_eval/env.txt
bash yolo_eval/setup_venv.sh
bash yolo_eval/bench_tensorrt.sh
yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --date 2026-09-23 --debut 13:21:00 --duree 1440
python3 -m http.server 8001 --directory yolo_eval/out   # puis http://100.116.160.30:8001/
```

### Zone convoyeur ajoutée à l'évaluation YOLO (mise à jour du 2026-09-27)

Nouvelle demande reçue pour "mettre en place et tester YOLO sur le
Jetson" : ce travail **existe déjà en très grande partie** depuis le
2026-09-26 (section ci-dessus, `yolo_eval/`) — modèle choisi (YOLO11n),
script isolé (`eval_yolo.py`), mesures de FPS/qualité, rapport complet.
Pas de nouveau dossier créé (la demande en suggérait un, `yolo_eval/`,
déjà pris) ; le point réellement manquant a été ajouté à l'existant :
**une classification spécifique "zone convoyeur"** (distincte de
`zone_machine`, qui est l'union des 4 zones) et un log explicite
présence oui/non sur cette zone précise — c'est celle où le système par
pixels (`signature_eval/signature.py`) et l'inspection visuelle ratent le
plus souvent le conducteur (immobile, masqué par le poteau/la pile).

**Changements dans `yolo_eval/eval_yolo.py`** (additifs, rétrocompatibles) :
- `ZONE_CONVOYEUR = (700, 150, 790, 250)` — même rectangle que
  `signature_eval/signature.py` (dupliqué en dur, comme partout ailleurs
  dans ce dossier, pour ne dépendre d'aucun autre fichier).
- Chaque boîte détectée est maintenant classée sur DEUX zones
  indépendantes (`in_zone(xyxy, ZONE_MACHINE)` et
  `in_zone(xyxy, ZONE_CONVOYEUR)`, la fonction `in_zone` acceptait déjà un
  paramètre `zone`, réutilisée telle quelle).
- `detections.csv` : deux colonnes ajoutées, `nb_zone_convoyeur` et
  `presence_convoyeur` (oui/non) — insérées après `nb_zone_machine`,
  colonnes existantes intactes (même noms, même contenu) : aucun script
  qui lit ce CSV par nom de colonne (`select_candidates.py`) n'est
  affecté (vérifié en relisant son code).
- `resume.txt` : nouvelle ligne "images avec presence_convoyeur=oui" avec
  le taux correspondant.
- Images annotées : la zone convoyeur est maintenant dessinée en magenta
  (en plus du cadre cyan de la zone machine), et l'étiquette affiche
  aussi le nombre de détections "dont convoyeur". Exemple :
  `yolo_eval/resultats_2026-09-26/zone_convoyeur_ajoutee.jpg`.

**Testé** (57 vraies images caméra 15, seules disponibles ici - toujours
aucun accès réseau au DVR/Jetson depuis cet environnement, revérifié) :
aucune exception, CSV cohérent, rendu visuel correct (capture ci-dessus).
Résultat : **0/57 `presence_convoyeur=oui`** — confirme une fois de plus,
avec le nouveau mécanisme de log explicite cette fois, ce qui était déjà
documenté (0/15 sur le même principe le 2026-09-26) : YOLO générique ne
voit jamais le conducteur dans cette zone précise. 1/57 `zone_machine`
(le même cas connu, zone "tête", hors convoyeur).

**Comparaison qualitative demandée (YOLO vs `signature_eval/signature.py`)** :
| Limite | Système par pixels (signature.py) | YOLO |
|---|---|---|
| Bruit de mouvement / éclairage (faux positif) | Oui, avant le correctif `MIN_FRAC_PRESENCE` de la veille (voir section dédiée) - une variation de pixels seule pouvait déclencher un "OUI" | Non concerné par ce type de bruit (détecte une silhouette, pas un changement de pixels) mais rate presque toujours le conducteur assis/masqué (0/57 ici) |
| Pose statique (conducteur immobile) | Peut manquer une présence si le mouvement est trop faible pour dépasser le seuil de différence | Ne dépend pas du mouvement, seulement de la silhouette - mais la pose penchée/statique typique au convoyeur reste rarement reconnaissable par un YOLO générique |
| Occlusion partielle (poteau, pile de cartons) | Le seuil de fraction de pixels vus (`MIN_FRAC_PRESENCE`, 0,28) compense en partie en exigeant une portion minimum visible | YOLO échoue presque systématiquement dès que la silhouette humaine standard est coupée (déjà documenté : 0/15 puis 0/57 au convoyeur) |

**Conclusion inchangée par rapport au 2026-09-26** : YOLO reste peu utile
seul pour la zone convoyeur spécifiquement (voir recommandation complète
dans "Signature de couleur : conclusion") ; le nouveau mécanisme de log
convoyeur-spécifique est désormais disponible pour tout test futur (y
compris sur les données multi-jours en préparation, voir
"Validation multi-jours de la signature de couleur").

### Diagnostic 0/57 zone convoyeur (mise à jour du 2026-09-27)

Suite au 0/57 déjà observé pour `presence_convoyeur` : avant de conclure
que YOLO échoue réellement, deux biais possibles du TEST lui-même ont
été écartés méthodiquement (aucun fichier de production ni
`signature_eval/signature.py` touché ; extension de
`yolo_eval/eval_yolo.py` uniquement, décrite dans son en-tête).

**Méthode** : nouveau mode `--diag-convoyeur` (voir docstring du
script) — une seconde passe d'inférence à seuil quasi nul (`--diag-conf
0.01`, contre 0,30 en usage normal) capture TOUTES les détections
"person" candidates, pour chacune desquelles est calculé, en plus du
test officiel centre-dans-zone (`in_zone`, inchangé) : l'IoU et la
fraction de la boîte recouverte par la zone convoyeur (`overlap_metrics`,
nouvelle fonction) — ce test ne dépend pas du centre, donc détecte aussi
les boîtes qui débordent sans que leur centre soit dans la zone.
`--verite-terrain` restreint l'analyse des scores aux images où la
présence a été confirmée à l'œil (15 des 57 images, reprises de
`occ.json`/la vérification manuelle déjà faite pour le test signature de
couleur). `--marge-convoyeur-px` permet en plus de tester concrètement
un agrandissement de la zone officielle.

**Testé** sur les 57 vraies images caméra 15 (seules disponibles ici,
toujours pas d'accès DVR/Jetson) :

**Hypothèse 2 (seuil de confiance trop haut) : écartée.** Même à
`--diag-conf 0.01` (quasi aucun filtrage), **aucune détection n'a son
centre dans la zone convoyeur**, sur les 57 images ET sur les 15 images
confirmées manuellement. Le modèle n'échoue pas parce que ses
détections sont filtrées : il ne produit tout simplement **aucune
boîte candidate** dans cette zone, à aucune confiance. Confirmé
visuellement : `yolo_eval/resultats_2026-09-26/diagnostic_convoyeur_conf001.jpg`
(13:20:01, présence confirmée au convoyeur — 14 boîtes candidates à
conf≥0,01 ailleurs dans l'image, aucune ne touche la zone convoyeur,
même en bord de cadre).

**Hypothèse 3 (boîte déborde sans que le centre y soit) : écartée.**
Sur 990 détections candidates générées (toutes images, conf≥0,01), 2
seulement ont un chevauchement non nul avec la zone convoyeur sans que
leur centre y soit — et leur recouvrement est négligeable (IoU 0,006 à
0,009, 2 à 3 % de la boîte). Aucune de ces 2 détections marginales n'est
sur une image de vérité-terrain (conducteur confirmé) : ce sont des
boîtes ailleurs dans l'image qui touchent à peine le rectangle par
coïncidence, pas des indices d'un conducteur mal cadré.

**Ajustement testé concrètement (conf=0,01 + marge zone +30px, le
maximum de permissivité raisonnable)** : le taux passe de 0/57 à
**1/57** — mais ce gain est un **faux positif**, pas une vraie
détection retrouvée : la seule image concernée (13:20:11) n'est PAS
dans les 15 confirmées au convoyeur ; c'est le cas déjà connu et
documenté d'une personne détectée dans la zone "tête" (voir "Évaluation
YOLO isolée" plus haut), dont une boîte fragmentaire à très faible
confiance (0,03) déborde dans la zone convoyeur élargie par la marge.
Élargir la zone ou baisser le seuil n'a donc pas récupéré le conducteur
manqué ; ça a introduit un risque de faux positif sur une détection déjà
correctement classée ailleurs.

**Conclusion (point 5 de la demande) : le 0/57 est un vrai échec du
modèle, pas un artefact du test.** Confirmé avec des exemples précis
(image ci-dessus) : sur les 15 images où le conducteur est physiquement
visible au convoyeur (bien que penché, immobile, ou partiellement masqué
par le poteau/la pile de cartons), YOLO11n ne produit littéralement
aucune boîte candidate à cet endroit, à aucun niveau de confiance, dans
aucun rayon raisonnable autour de la zone. Ceci confirme et renforce
(avec des données, pas seulement une observation qualitative) la
conclusion déjà tirée le 2026-09-26 et le 2026-09-27 : YOLO générique
est structurellement inadapté à cette pose/cet angle spécifiques, pas
seulement mal réglé - cohérent avec la recommandation déjà documentée
d'utiliser le système par pixels + signature de couleur (ou un futur
modèle réentraîné sur des images de cette caméra) plutôt que d'espérer
gagner en ajustant les seuils de YOLO.

Nouveaux paramètres `eval_yolo.py` (rétrocompatibles, défauts =
comportement identique aux runs précédents) : `--diag-convoyeur`,
`--diag-conf` (0.01), `--verite-terrain`, `--marge-convoyeur-px` (0). Le
comportement par défaut (sans ces options) est strictement inchangé.

### Évaluation d'une signature de couleur vestimentaire (mise à jour du 2026-09-26)

Suite du test YOLO (ci-dessus) : YOLO ne voit jamais le conducteur penché
au convoyeur (0/15). Piste testée ici, isolée dans `signature_eval/` :
l'identifier par la couleur dominante de ses vêtements plutôt que par sa
silhouette (chaque employé porte ses propres vêtements, donc un signal
potentiellement distinctif). **Aucun fichier de production ni de
`yolo_eval/` n'a été modifié.**

**Recherche d'une image "conducteur visible ailleurs"** : sur les 57
images réelles, une seule contient une personne détectée (par YOLO) à
l'intérieur d'une des 4 zones de la machine suivie, hors convoyeur :
`159cdc7c-image.jpg.png` (11:40:51), zone `tete`, une personne en haut
clair près de l'escalier. Toutes les autres détections YOLO du jeu de 57
images sont soit un ouvrier récurrent à une **autre machine** (chemise
bleue, coin haut-gauche, ~40/57 images — sert de témoin "autre
personne"), soit un passant ou un ouvrier au bord du champ, sans rapport
avec la machine suivie. **Limite importante** : cette unique image n'est
pas confirmée comme étant le même conducteur (aucune vérité terrain) ; le
jeu de données (57 captures DMSS sur ~20 minutes) ne permet pas de
trouver mieux. Toute conclusion ci-dessous doit être lue comme un test de
faisabilité, pas une validation statistique.

**Méthode** (voir `signature_eval/signature.py`, choix documentés dans
le code) :
1. Masque de premier plan = pixels différents d'une image de référence
   "machine vide" (`reference_fragments_real.png`, médiane des 57
   images), même principe que la détection par fragments en production.
2. Signature = couleur **médiane** des pixels du masque en espace **Lab**.
3. Comparaison = distance sur la **chrominance seule (a, b)**, la
   luminance L étant écartée (voir résultat ci-dessous).

**Pourquoi écarter la luminance L** : mesurée d'abord avec la distance
Lab complète, la couleur variait énormément d'une image convoyeur à
l'autre (L de 61 à 160 sur les 15 images) à cause de l'éclairage, des
reflets, et surtout du fait que **la pile de feuilles à côté du
convoyeur bouge aussi** (elle est réapprovisionnée/consommée), contaminant
le masque de différence au même titre que le corps du conducteur (c'est
la même limite déjà documentée pour la détection de présence — dérive de
palette, cf. section "Zone convoyeur ajoutée" plus haut). En ne comparant
que la chrominance (a, b), le bruit de mesure chute fortement :

| Comparaison | Distance moyenne (chrominance a,b) |
|---|---|
| Les 15 images convoyeur **entre elles** (même personne, même zone) | **1.7** (max 2.8) |
| Les 15 images convoyeur vs témoin (ouvrier à la chemise bleue, autre machine) | **16.1** |
| Les 15 images convoyeur vs signature "conducteur ailleurs" (159cdc7c) | **8.6** |

**Lecture** : les 15 mesures au convoyeur sont très cohérentes entre
elles (bruit ~1.7-2.8) — cohérent avec **le même vêtement filmé
plusieurs fois**, malgré une visibilité très partielle (9 à 30 % de la
zone visible selon l'image, le reste caché par la poutre/le poteau). Elles
sont nettement plus proches de la signature "conducteur ailleurs" (8.6)
que du témoin à chemise bleue (16.1) — un facteur ~2. Avec le seuil
choisi (8.0, voir justification dans le code), 8 des 15 images
correspondent ; les 7 autres sont juste au-dessus (9.2 à 11.2), cohérent
avec un léger décalage de teinte dû à l'éclairage local du convoyeur
(plus chaud que celui de la zone où la signature a été prise, 17 minutes
plus tôt) plutôt qu'avec une personne différente.

**Cas de confusion (autre ouvrier)** : le témoin (chemise bleue, autre
machine) est nettement séparé (16.1, quasi 2x plus loin que la signature
conducteur) — sur cet exemple, pas de risque de confusion visible entre
les deux personnes.

**Images annotées** : `signature_eval/resultats_2026-09-26/` —
`signature_source.jpg` (référence), `temoin_autre_ouvrier.jpg` (témoin),
`convoyeur_01.jpg`/`convoyeur_02.jpg` (deux cas convoyeur, masque en
surimpression). Détail chiffré : `mesures.csv`, `resume.json`.

**Conclusion : piste prometteuse mais non validée, à ne pas déployer en
l'état.** Le signal (chrominance) sépare nettement les deux personnes
observées ici, mais avec des limites sérieuses :
- **Un seul exemple de signature de référence** — impossible de savoir si
  la variabilité normale de la même personne (vêtements différents selon
  les jours, éclairage) dépasse le seuil retenu.
- **Portion visible très faible au convoyeur** (9-30 % de la zone,
  souvent < 15 %) : fiable seulement si le masque tombe majoritairement
  sur du tissu, pas sur la pile de feuilles ou le flou de la poutre — ce
  qui arrive (contamination visible sur certaines images, notamment
  `10d12a94` où une partie du masque touche la pile).
- **La luminance ne peut pas servir** (trop instable), donc la méthode
  perd toute information de "clair/foncé" et ne distinguerait pas deux
  vêtements de même teinte mais de luminosité très différente (ex. bleu
  clair vs bleu marine).

**Recommandation** :
1. Ne pas remplacer/compléter la détection par fragments avec cette
   méthode pour l'instant — le jeu de preuves est trop mince (1 signature,
   1 témoin).
2. Si la piste est retenue, constituer un vrai jeu de données avant
   d'aller plus loin : plusieurs captures du **même** conducteur, debout
   et bien visible à différents endroits de la machine, le même jour que
   des passages au convoyeur, pour valider un seuil de chrominance sur
   plusieurs personnes réelles (pas un seul témoin).
3. Méthode recommandée si ce jeu de données existe : couleur **médiane
   en chrominance (a, b) de Lab**, masque par différence au fond de
   référence, seuil à recalibrer sur les vraies données (celui utilisé
   ici, 8.0, est une estimation prudente à mi-chemin entre le bruit
   intra-personne mesuré, ~1.7-2.8, et l'écart inter-personnes mesuré,
   ~16).
4. Utiliser des images issues directement du flux RTSP (via
   `debug_frames/`), pas des captures d'écran DMSS recompressées : la
   compression/le redimensionnement des captures utilisées ici dégrade
   la précision de couleur disponible.

### Vérification du flux d'enregistrement DVR (mise à jour du 2026-09-26)

Question posée : le DVR Dahua enregistre-t-il en continu la caméra 15 en
**mainstream** (HD) ou **substream** (SD) ? C'est un réglage du planning
d'enregistrement du DVR, indépendant du flux demandé en lecture. Vérification
isolée dans `dvr_check/` (lecture seule, **aucun réglage du DVR modifié,
aucun fichier de production/`yolo_eval/`/`signature_eval/` touché**).

**Ce qui a pu être vérifié depuis l'environnement de développement**
(DVR/Jetson injoignables, Tailscale hors de portée, confirmé à nouveau) :
- Le code du projet (`src/camera_stream.py`) demande **toujours
  `subtype=0`** (flux principal, convention Dahua : 0 = main, 1 = sub),
  pour le direct comme pour la lecture des enregistrements
  (`/cam/playback`). Ceci est inchangé depuis la création du projet.
- **Mais ceci ne prouve rien sur ce que le DVR enregistre réellement** :
  si le DVR est configuré pour n'enregistrer en continu que le substream,
  demander `subtype=0` en lecture peut échouer, renvoyer un flux vide, ou
  (selon le firmware) renvoyer quand même le seul flux disponible malgré
  le paramètre demandé. Seule la configuration du DVR fait foi.
- **Point important découvert en documentant ceci** : la valeur
  « résolution du flux principal : 1280x720 » (section "État actuel"
  ci-dessus) n'a jamais été mesurée directement sur un enregistrement RTSP
  brut depuis ce projet — c'est une information transmise par Mohamed avant
  réception du matériel. Les 57 images réelles utilisées pour les tests
  YOLO et signature de couleur (`yolo_eval/`, `signature_eval/`) sont des
  **captures d'écran de l'appli DMSS** (téléphone), recadrées puis
  **redimensionnées manuellement à 1280x720** pour correspondre à cette
  valeur supposée — ce n'est donc pas non plus une mesure fiable de la
  résolution réelle du flux enregistré par le DVR. La seule image générée
  à partir d'un vrai flux RTSP du Jetson (`reference_fragments.png`,
  produite le 2026-09-23 par `compute_reference_fragments.py`) n'est pas
  commitée dans le dépôt (fichier généré, non versionné) : sa résolution
  n'a pas pu être revérifiée ici.

**Donc : ni le réglage d'enregistrement du DVR, ni la résolution réelle
des enregistrements déjà utilisés pour les tests, n'ont pu être confirmés
depuis cet environnement.** Ce sujet reste ouvert tant que les commandes
suivantes n'ont pas été lancées depuis le Jetson :

```bash
cd ~/suivi-de-machine && git pull
bash dvr_check/check_stream_config.sh          # config DVR : resolution/bitrate main vs sub, planning
python3 dvr_check/check_recording_resolution.py --image reference_fragments.png
```

Voir `dvr_check/README.md` pour le détail, le chemin dans l'interface web
si le CGI de planning ne donne rien d'exploitable, et la marche à suivre
**si un changement s'avère nécessaire (à ne faire qu'après validation
explicite de Mohamed)** : Réglages → Stockage → Planning d'enregistrement,
changer le flux programmé de Secondaire/Sub vers Principal/Main pour la
caméra 15 — en gardant à l'esprit qu'un enregistrement en mainstream est
plus volumineux (plus d'espace disque utilisé, rétention réduite sur le
DVR).

### YOLO et signature sur flux DVR réel (mise à jour du 2026-09-26, non exécuté ici)

Suite de la vérification précédente (mainstream réel confirmé : caméra 15,
1280x720, H.264, 15 fps). Objectif : refaire les tests YOLO
(`yolo_eval/`) et signature de couleur (`signature_eval/`) sur des images
extraites **directement du flux DVR**, pour comparer avec les résultats
obtenus sur les 57 captures d'écran DMSS (doublement compressées :
compression DMSS + capture d'écran + redimensionnement manuel).

**Bloqué depuis cet environnement** : toujours aucun accès réseau au
DVR/Jetson (revérifié le 2026-09-26). Aucune nouvelle image n'a donc pu
être extraite ni testée ici — ce qui suit est l'outillage préparé, à
exécuter sur le Jetson, pas un résultat.

**Choix de la plage horaire** : le 2026-09-05 11:48–12:00 (utilisé pour
les 57 captures DMSS) date de **21 jours** avant aujourd'hui, contre une
rétention DVR de **17 jours** confirmée précédemment — cet enregistrement
est très probablement déjà écrasé. `dvr_check/list_recordings.sh` permet
de le vérifier précisément (liste les fichiers d'enregistrement d'un jour
donné via le CGI Dahua `mediaFileFind`), mais **la plage de repli
2026-09-23 13:21–13:45** (3 jours, déjà utilisée par `yolo_eval/eval_yolo.py`,
donc sûrement encore disponible) est recommandée par défaut plutôt que de
perdre du temps à vérifier une plage presque certainement absente.

**Outillage ajouté (isolé, aucun fichier de production/yolo_eval/
signature_eval existant modifié)** :
- `dvr_check/list_recordings.sh AAAA-MM-JJ` : liste les enregistrements
  disponibles pour la caméra 15 un jour donné (lecture seule).
- `signature_eval/extract_frames_dvr.py` : extrait des frames brutes
  directement du flux mainstream RTSP (réutilise
  `src.camera_stream.build_rtsp_playback_url`/`open_stream`, comme
  `yolo_eval/eval_yolo.py`), une image par seconde par défaut, qualité
  JPEG 95, **aucun redimensionnement manuel** (résolution native du flux
  décodé, 1280x720 attendu) - contrairement aux captures DMSS utilisées
  jusqu'ici.
- `signature_eval/build_reference.py` : construit une image de référence
  "machine vide" (médiane) à partir d'un dossier de frames, **vers un
  fichier de sortie choisi** — volontairement distinct de
  `compute_reference_fragments.py` (utilitaire de production existant, qui
  écrit toujours dans `reference_fragments.png`, le fichier utilisé par
  `src/fragment_detection.py` : le relancer aurait écrasé la référence de
  production, ce qui est exclu ici).
- `yolo_eval/eval_yolo.py` et `signature_eval/signature.py` sont réutilisés
  **sans aucune modification** (déjà capables de lire directement le
  flux DVR ou un dossier de frames).

**Marche à suivre complète** (commandes exactes) : voir
`yolo_eval/resultats_dvr_reel/README.md` et
`signature_eval/resultats_dvr_reel/README.md` — ces deux dossiers sont
créés vides (avec le mode d'emploi) en attendant l'exécution sur le
Jetson ; les résultats déjà obtenus le 2026-09-26 sur les captures DMSS
(`yolo_eval/resultats_2026-09-26/`, `signature_eval/resultats_2026-09-26/`)
ne sont pas modifiés.

**Limite anticipée pour l'étape 5 (signature de couleur)** : sur la plage
2026-09-23 13:21–13:45, aucune image "conducteur visible ailleurs sur la
machine" n'est connue à ce jour (le seul exemple utilisé jusqu'ici vient
du jeu du 2026-09-05, DMSS). Il faudra probablement inspecter une plage
plus large avant de retrouver un tel moment ; documenté comme limite
ouverte dans `signature_eval/resultats_dvr_reel/README.md`.

**Ce qui reste donc à faire (sur le Jetson, pas ici)** : lancer les
commandes ci-dessus, puis comparer concrètement le taux de détection
YOLO au convoyeur (0/15 sur DMSS) et le bruit de mesure de la signature
de couleur (intra ~1,7 en a,b sur DMSS) à ce qu'on obtient sur des images
non compressées manuellement — cette comparaison ne peut être faite
qu'une fois ces commandes exécutées.

### Correctif curl `--globoff` sur `dvr_check/list_recordings.sh` (mise à jour du 2026-09-26)

`list_recordings.sh` échouait ("curl: (3) bad range in URL position 200")
sur l'appel `findFile` : l'URL contient `condition.Types[0]=dav`, un
paramètre littéral requis par le CGI Dahua `mediaFileFind`, mais curl
interprète par défaut `[0]` comme sa propre syntaxe de génération d'URLs
(globbing), pas comme du texte. L'authentification (`--digest`) n'était
pas en cause.

**Correctif** : ajout de `-g` (`--globoff`, désactive le globbing) à cet
appel curl. Les autres appels curl du dépôt (`dvr_check/check_stream_config.sh`,
les autres appels de `list_recordings.sh`) ne construisent aucune URL avec
des crochets littéraux — vérifié, aucun autre correctif nécessaire.

### Correctif `yolo_eval/eval_yolo.py` : dossiers `frame_HHMMSS.jpg` sans date (mise à jour du 2026-09-26)

`frames_from_folder` plantait (`ValueError: time data 'frames' does not
match format '%Y-%m-%d'`) sur les dossiers produits par
`signature_eval/extract_frames_dvr.py`, nommés librement (pas
`AAAA-MM-JJ_HHMMSS` comme `debug_frames/`) et contenant des fichiers
`frame_HHMMSS.jpg` sans date.

**Correctif** : si le nom du dossier ne commence pas par une date
`AAAA-MM-JJ`, utilise la date du jour du traitement comme repli. Sans
risque : la date ne sert qu'à construire l'horodatage affiché/sauvegardé
(moments clés, noms des images annotées), jamais la logique de
détection ; les heures HH:MM:SS restent lues depuis le nom de chaque
fichier (`FRAME_RE`), donc inchangées et correctes quel que soit le
dossier. Testé avec deux images réelles renommées `frame_HHMMSS.jpg` sans
date dans le nom du dossier : horodatages corrects, plus d'exception.

### Signature multi-référence sur flux DVR réel (mise à jour du 2026-09-26, données introuvables)

Demande : refaire le test de signature de couleur sur les 1440 images
DVR réelles (2026-09-23 13:21-13:45) censées être dans
`signature_eval/resultats_dvr_reel/frames_extraites/`, avec plusieurs
candidats "conducteur ailleurs" choisis automatiquement à partir du
`detections.csv` YOLO censé être dans `yolo_eval/out/frames_extraites/`.

**Ces fichiers sont introuvables dans cet environnement** : recherche
faite sur le disque local (dossier de travail, scratchpad de session) et
sur le dépôt Git, y compris après `git fetch origin main` (aucun nouveau
commit, ces chemins ne sont ni dans l'historique ni sur la branche
distante). Aucune commande de la tâche précédente n'a donc pu être
exécutée par personne dans le dépôt visible ici. Rappel : ces sorties
(`yolo_eval/out/`, `signature_eval/frames_dvr_*/`) sont volontairement
gitignorées (volumineuses, régénérées à la demande) — si elles ont été
produites sur le Jetson, elles y restent tant qu'on ne les récupère pas
explicitement ; ce n'est pas automatique.

**Conséquence** : impossible de produire la comparaison demandée
(candidats retenus, taux de correspondance, comparaison avec le test
DMSS) — ce serait inventer des résultats. Ce qui suit est l'outillage
préparé et testé, prêt à être utilisé dès que ces données seront
disponibles (sur le Jetson, ou remontées ici).

**Outillage ajouté (testé, aucun fichier de production touché)** :

- **`signature_eval/signature.py` accepte maintenant plusieurs images de
  référence** via `--signature-manifest <fichier.csv>` (lignes
  `image,x1,y1,x2,y2`), en plus du mode à une seule image
  (`--signature-image`/`--signature-box`, conservé et **rétrocompatible**
  - revérifié : mêmes résultats qu'avant sur les captures DMSS,
  distance_ab_moyenne_convoyeur_vs_signature = 8.6, identique). La
  signature retenue devient la **médiane des couleurs mesurées sur
  chaque candidat** (plus robuste qu'un seul exemple), et une nouvelle
  métrique `distance_ab_intra_signature_*` mesure la cohérence ENTRE les
  candidats eux-mêmes (doit être petite si ce sont bien tous le même
  conducteur — sert de garde-fou à la vérification visuelle demandée à
  l'étape 2).
- **`signature_eval/select_candidates.py`** : lit un `detections.csv`
  produit par `eval_yolo.py` et sélectionne automatiquement des candidats
  "ailleurs sur la machine, **hors zone convoyeur**" (la zone
  `zone_machine` d'`eval_yolo.py` est l'union des 4 zones y compris le
  convoyeur — ce script exclut explicitement le rectangle convoyeur pour
  ne garder que les vraies détections "ailleurs"), répartis dans le temps
  (une par tranche de la plage étudiée, la plus confiante). Testé avec un
  CSV synthétique reproduisant le format exact d'`eval_yolo.py` (5 lignes,
  dont une détection au convoyeur à exclure et une ligne à 2 boîtes) :
  sélection correcte des 3 candidats attendus, exclusion correcte du
  convoyeur.
- `yolo_eval/eval_yolo.py` n'a pas été modifié (déjà suffisant pour
  produire le `detections.csv` consommé par `select_candidates.py`).
- `signature_eval/resultats_dvr_reel_v2/` créé avec le mode d'emploi
  complet (commandes exactes), en attente des vraies données ; les
  résultats existants (`resultats_2026-09-26/`, `resultats_dvr_reel/`) ne
  sont pas modifiés.

**Ce qui reste à faire, une fois les données disponibles (sur le
Jetson)** : lancer `select_candidates.py`, **vérifier visuellement**
chaque candidat retenu (étape 2 de la demande, non automatisable),
construire la référence "machine vide" à partir des mêmes frames DVR
(`build_reference.py`), relister les 15 instants convoyeur avec les noms
de fichiers réels, puis lancer `signature.py --signature-manifest` et
comparer `resume.json` à celui du test DMSS
(`resultats_2026-09-26/resume.json`) — voir
`signature_eval/resultats_dvr_reel_v2/README.md` pour les commandes
exactes.

### Correctif OOM `signature_eval/build_reference.py` (mise à jour du 2026-09-26)

`build_reference.py` était tué par le système ("Killed") sur le Jetson en
traitant les 1440 images DVR réelles (2026-09-23 13:21-13:45,
1280x720) malgré 6,3 Gio de RAM libre.

**Cause identifiée** : le script chargeait TOUTES les images en mémoire
d'un coup (`[cv2.imread(p) for p in paths]`, ~3,8 Go pour 1440 images
1280x720), puis `np.stack(...)` en faisait une **deuxième copie complète**
pour les empiler avant `np.median` — les deux copies coexistant un
instant, l'empreinte réelle atteignait près du double de 3,8 Go,
dépassant les 6,3 Gio disponibles.

**Correctif** : traitement par **lots** (`--lot`, 100 images par défaut
≈ 260 Mo, marge large). Pour chaque lot : lecture, médiane du lot (calcul
en float32, pas le float64 par défaut de numpy, pour limiter l'empreinte
mémoire du tri interne), puis libération explicite avant le lot suivant.
La référence finale est la **médiane des médianes de lot** — une
approximation standard du calcul en flux d'une médiane globale, valable
tant que le conducteur reste minoritaire dans chaque lot (cas normal sur
24 minutes d'enregistrement). Avec `--lot` supérieur ou égal au nombre
total d'images, un seul lot est utilisé et le résultat est la **médiane
exacte** (comportement identique à l'ancienne version).

**Vérifié** (57 vraies images caméra 15, seule taille disponible ici) :
- Mode un seul lot vs calcul direct non optimisé (`np.median` sur toutes
  les images chargées d'un coup) : résultat **identique au pixel près**
  (diff = 0).
- Mode multi-lots (6 lots de 10 images) vs médiane exacte : différence
  négligeable (moyenne 0,46/255 par pixel/canal, seulement 0,17 % des
  pixels avec un écart > 5/255, quelques pixels isolés jusqu'à 171 —
  cohérent avec la contamination attendue par le conducteur ou un autre
  élément mobile dans certains lots).

**Reste à faire (sur le Jetson, pas testable ici sans les 1440 images
réelles ni la contrainte mémoire du Jetson)** : relancer
`build_reference.py` sur `signature_eval/resultats_dvr_reel/frames_extraites/`
(1440 images) et confirmer que ça ne plante plus. Le réglage par défaut
(`--lot 100`, ~14 lots pour 1440 images) devrait largement suffire ; si
la mémoire le permet, un `--lot` plus grand (ex. 500-1440) rapproche le
résultat de la médiane exacte, au prix d'un peu plus de mémoire par lot.

### Signature de couleur : conclusion (mise à jour du 2026-09-26)

Synthèse des deux tests menés (détail de chacun dans les sections
précédentes) :

| | Test 1 — captures DMSS (05/09) | Test 2 — images DVR natives (23/09) |
|---|---|---|
| Source des images | captures d'écran de l'appli DMSS, redimensionnées à la main (doublement compressées) | flux DVR mainstream réel, 1280x720, aucune recompression manuelle |
| Images de référence ("conducteur ailleurs") | 1 (trouvée par hasard, non confirmée comme étant sûrement le conducteur) | 3 (sélectionnées automatiquement par `select_candidates.py`, **vérifiées visuellement** comme étant bien le conducteur) |
| Instants convoyeur testés | 15 | 10 |
| Cohérence intra-convoyeur (a,b) | 1,7 (max 2,8) | 1,44 (max 3,49) |
| Cohérence intra-référence (a,b) | non calculable (1 seule image) | 2,75 (max 6,0) |
| Écart signature ↔ convoyeur | 8,6 | **4,19** |
| Écart vs témoin (autre ouvrier) | 16,1 | non remesuré dans ce second test |
| Correspondances au seuil (8,0) | 8/15 | **10/10** |

Résultats détaillés du test 2 (JSON, logs) : produits sur le Jetson dans
`signature_eval/out_dvr_reel_v2/` — ce dossier est un dossier de travail
(comme `signature_eval/out/`), **pas encore recopié** dans
`signature_eval/resultats_dvr_reel_v2/` (git), qui ne contient pour
l'instant que le mode d'emploi. À faire en suivi : copier le sous-ensemble
pertinent (resume.json, quelques images annotées) comme pour les tests
précédents, pour que la trace chiffrée soit consultable depuis le dépôt.

**Pourquoi la meilleure qualité source améliore le résultat** : l'écart
signature/convoyeur passe de 8,6 à 4,19 (environ 2 fois plus net), et le
taux de correspondance au seuil passe de 8/15 à 10/10. Deux causes
cumulées, cohérentes avec les limites déjà identifiées :
1. **Moins de bruit de compression** : les captures DMSS subissaient une
   double compression (encodage DMSS + capture d'écran + redimensionnement
   manuel à la volée), qui aplatit et bruite la chrominance — la source
   du problème de "luminance instable" documenté dans le test 1. Les
   images DVR natives évitent cette perte.
2. **Plusieurs références au lieu d'une** : la signature du test 2 est la
   médiane de 3 mesures (vérifiées) plutôt qu'un seul exemple non
   confirmé — mécaniquement plus robuste à une mesure isolée biaisée
   (reflet, angle, ombre).

**Verdict : la méthode est significativement renforcée, mais n'est pas
encore considérée comme validée pour un déploiement en production.**
Réserves qui subsistent :
- **Volume de données toujours limité** : 3 références + 10 instants
  convoyeur, un seul jour, une seule plage horaire (24 minutes) — pas de
  test sur plusieurs jours, éclairages (matin/après-midi/nuit), ou
  saisons de vêtements différentes.
- **Pas de témoin (autre ouvrier) remesuré sur ce second test** : le test
  1 avait montré une séparation ~2x envers un autre ouvrier (chemise
  bleue) ; ce résultat n'a pas été reconfirmé avec la méthode/qualité
  actuelle sur le test 2. Tant que ce n'est pas refait, le risque de
  confusion avec un autre ouvrier portant une couleur proche reste
  non quantifié sur données récentes.
- **Seuil (8,0) toujours choisi à la main**, pas calibré statistiquement
  sur un jeu de données assez large pour fixer un taux de faux positifs/
  négatifs cible.
- **Un seul conducteur suivi pour l'instant** : la méthode n'a pas été
  pensée ni testée pour plusieurs conducteurs/machines en parallèle.
- **Robustesse au changement de tenue non testée** : la signature suppose
  la même tenue d'un jour à l'autre ; rien ne détecte ni ne compense un
  changement (vêtement différent, saison).

### Recommandation d'intégration

**Ne pas remplacer la détection par fragments** (méthode de production
actuelle) par la signature de couleur — celle-ci reste un signal
complémentaire, pas un remplacement :

1. **Niveau d'intégration proposé** : à l'intérieur de
   `src/fragment_detection.py`, comme **confirmation optionnelle** sur
   les sessions déclenchées par la zone convoyeur (celle où YOLO échoue
   et où la fragmentation seule ne dit rien sur l'identité). Concrètement
   : quand une présence convoyeur est confirmée (durée minimum atteinte),
   calculer la signature de couleur de la zone et l'enregistrer comme un
   champ supplémentaire de la session (`sessions.csv`), sans jamais
   bloquer ni invalider la détection de présence elle-même — un ajout
   d'information, pas une nouvelle condition.
2. **Étapes techniques nécessaires avant tout déploiement réel** :
   - Reconstituer un jeu de validation plus large (plusieurs jours,
     plusieurs plages horaires) avec, à chaque fois, un témoin (autre
     ouvrier) mesuré pour reconfirmer la séparation signature/témoin sur
     données récentes.
   - Recalibrer `MATCH_THRESHOLD_AB` statistiquement sur ce jeu élargi
     plutôt qu'à la main.
   - Automatiser le **réenrôlement** de la signature en début de poste
     (via `select_candidates.py` + vérification visuelle rapide), pour
     absorber un changement de tenue d'un jour à l'autre sans casser la
     détection.
   - Si plusieurs conducteurs/machines doivent un jour être suivis en
     parallèle, prévoir une signature par machine/poste (pas un
     changement structurel majeur, mais à concevoir avant d'étendre).
   - Faire tourner le calcul de signature en tâche de fond (asynchrone)
     sur le Jetson pendant quelques jours, en loggant seulement (sans
     agir dessus), pour comparer a posteriori aux observations humaines
     avant de lui donner un rôle actif dans le tableau de bord.

### Validation multi-jours de la signature de couleur (mise à jour du 2026-09-27, en préparation)

Suite à la conclusion du test à un seul jour (2026-09-23) : avant tout
déploiement, vérifier la stabilité de la méthode sur plusieurs jours
différents (éclairage variable, tenue du conducteur). **Bloqué comme
toutes les étapes précédentes nécessitant le DVR** : cet environnement de
développement n'a toujours pas d'accès réseau (revérifié). Rien n'a donc
pu être extrait ni analysé ici — ce qui suit est l'outillage préparé,
prêt à être lancé sur le Jetson, **et volontairement arrêté avant le
lancement final de `signature.py`**, en attente d'une vérification
visuelle humaine (demande explicite : cette vérification ne peut être
faite que par Mohamed).

**Jours proposés** (non vérifiés depuis ici, à confirmer avec
`dvr_check/list_recordings.sh`) : **2026-09-15** (mardi, 12 jours),
**2026-09-21** (lundi, 6 jours), **2026-09-25** (vendredi, 2 jours) — 3
jours de semaine différents, étalés dans la fenêtre de rétention (~17
jours, donc environ 2026-09-10 à 2026-09-27 aujourd'hui), en évitant le
bord le plus incertain de la rétention (2026-09-10/11). Plage horaire
proposée : **13:15:00, 30 minutes**, la même heure de journée que le test
déjà validé du 23/09 (13:21-13:45), élargie un peu pour augmenter les
chances de capturer plusieurs passages au convoyeur. Ces choix sont des
hypothèses raisonnables, pas des certitudes : à vérifier/ajuster au
premier lancement (si `test_fragments_on_recording.py` ne montre aucune
présence sur cette plage un jour donné, essayer une autre heure avant de
conclure à une indisponibilité).

**Outillage ajouté (isolé, testé quand testable sans DVR, aucun script
déjà validé modifié)** :

- **`signature_eval/prepare_day.sh`** : orchestre, pour un jour donné,
  les 5 étapes déjà utilisées manuellement pour le test du 23/09:
  1. instants "convoyeur" via `test_fragments_on_recording.py` (script de
     test existant, **réutilisé tel quel**, en lecture seule vis-à-vis de
     la production - il ne fait que lire l'enregistrement et journaliser),
     parsés par le nouveau `extract_convoyeur_instants.py` (repère les
     lignes `convoyeur=...*`, testé avec un journal synthétique reproduisant
     le format exact : extraction correcte, exclusion des `~` et lignes
     absentes) ;
  2. extraction des frames brutes (`extract_frames_dvr.py`, déjà existant) ;
  3. détection YOLO (`yolo_eval/eval_yolo.py`, **non modifié**) ;
  4. sélection automatique des candidats "conducteur ailleurs"
     (`select_candidates.py`, déjà existant) ;
  5. référence "machine vide" (`build_reference.py`, **non modifié**).
  S'arrête ensuite en affichant clairement quelles images vérifier
  visuellement, comment les voir depuis le téléphone (serveur http comme
  pour les tests précédents), et la commande `signature.py` prête à
  copier-coller **une fois la vérification faite** — jamais exécutée par
  le script lui-même.
- **`signature_eval/extract_convoyeur_instants.py`** (nouveau) : lit le
  journal texte de `test_fragments_on_recording.py` et en extrait les
  instants convoyeur, au format attendu par `signature.py
  --occluded-list`.
- `.gitignore` : ajout de `/sessions_test_fragments.csv` (sortie brute de
  `test_fragments_on_recording.py`, déplacée automatiquement par
  `prepare_day.sh` vers le dossier du jour).
- `signature_eval/resultats_multi_jours/` créé avec le protocole complet,
  les jours retenus et leur justification, et une liste de suivi à cocher
  (disponibilité DVR / préparation / vérification visuelle / lancement
  final, pour chacun des 3 jours) — sans toucher aux résultats existants
  (`resultats_2026-09-26/`, `resultats_dvr_reel_v2/`).

**Ce qui reste à faire, dans l'ordre, sur le Jetson** :
1. `bash dvr_check/list_recordings.sh <date>` pour chacune des 3 dates
   proposées (remplacer par le jour ouvré le plus proche si indisponible).
2. `bash signature_eval/prepare_day.sh <date> 13:15:00 1800` pour chaque
   jour retenu.
3. **Vérification visuelle par Mohamed** de chaque candidat "conducteur
   ailleurs" (`signature_eval/resultats_multi_jours/<date>/a_verifier/`),
   retrait des lignes douteuses du manifeste.
4. Lancer la commande `signature.py` affichée en fin de préparation, pour
   chaque jour.
5. Comparer les 3 résultats entre eux et avec le test du 23/09 (écart
   signature/convoyeur, cohérences intra-groupe) pour conclure sur la
   stabilité de la méthode d'un jour à l'autre — synthèse à documenter
   ici une fois faite.

### Correctif `signature_eval/signature.py` : seuil minimum de fraction avant comparaison de couleur (mise à jour du 2026-09-27)

**Bug identifié** : le script calculait `frac` (fraction de pixels
changés dans la zone convoyeur par rapport à la référence "machine
vide") mais ne l'utilisait jamais comme garde-fou avant de comparer la
couleur médiane à la signature du conducteur. Du bruit (variation
d'éclairage, reflet, léger mouvement de la machine) pouvait donc suffire
à déclencher un faux "OUI" même sans personne présente dans la zone : la
comparaison de couleur se faisait sur n'importe quelle fraction de pixels
changés, même minime et non représentative d'un vêtement.

**Correctif** : nouvelle constante `MIN_FRAC_PRESENCE = 0.28`, à côté de
`DIFF_THRESHOLD` et `MATCH_THRESHOLD_AB`. Avant de calculer les distances
et le résultat de correspondance, si `frac < MIN_FRAC_PRESENCE` : `match`
est forcé à `"non"`, la couleur n'est PAS ajoutée à la liste `colors`
(pour ne pas fausser les statistiques de cohérence intra-convoyeur et la
moyenne convoyeur/signature avec du bruit), et les distances ne sont même
pas calculées (pas de sens à comparer une couleur mesurée sur du bruit).
Valeur de départ (0,28) basée sur les mesures rapportées : bruit de fond
18-27% sur des images vérifiées vides, présence réelle 30-38% sur des
images confirmées - à recalibrer si de nouvelles mesures montrent un
chevauchement.

**CSV (`mesures.csv`) et logs** : nouvelle colonne `frac_suffisante`
(oui/non), pour distinguer en un coup d'œil une image rejetée pour
fraction insuffisante (`frac_suffisante=non`, `correspondance=non`) d'une
image avec assez de matière mais dont la couleur ne correspond pas
(`frac_suffisante=oui`, `correspondance=non`). Le cas "aucun pixel de
masque" (`color is None`) est inchangé (toujours `"aucun pixel"` dans la
colonne correspondance), une colonne `frac_suffisante=non` lui est
simplement ajoutée pour garder un CSV à nombre de colonnes constant.

**Vérifié** sur les 57 images DMSS (seules disponibles ici) : plus aucune
exception, CSV cohérent. Avec ce seuil (calibré sur les mesures DVR
natives, plus fiables), seule 1 des 15 anciennes images convoyeur DMSS
dépasse encore 28% (30,3%) - cohérent avec le fait que les captures DMSS,
plus bruitées/compressées, montraient déjà une fraction de pixels
changés généralement plus faible que les images DVR natives (voir
NOTES-SESSION.md, comparaison DMSS vs DVR réel) ; ce n'est pas un signe
de bug, mais la conséquence attendue d'un seuil calibré sur de meilleures
images appliqué à des images de moins bonne qualité.

Aucun autre fichier modifié (ni production, ni yolo_eval/).

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
