# Suivi de machine — Détection de présence par caméra

## Objectif

Suivre en temps réel la présence d'un conducteur/opérateur sur une machine
de l'atelier, à partir d'un flux caméra existant, afin de mesurer la durée
de chaque session de présence (horodatée, dans `sessions.csv`). Ce projet
est le socle qui alimentera plus tard, via une API HTTP, le projet
[`suivi-production-imprimerie`](https://github.com/simozougaa-ui/suivi-production-imprimerie).

**Ce dépôt est indépendant** : pas de dépendance de code ni de fusion avec
`suivi-production-imprimerie`. La communication entre les deux se fera
uniquement par appels HTTP (ce boîtier enverra ses résultats de détection à
l'API de suivi de production) — pas encore implémenté.

## Matériel en service

- **Boîtier de calcul** : NVIDIA Jetson Orin Nano Super, Ubuntu 24.04, en
  service à l'atelier.
- **Accès à distance** : [Tailscale](https://tailscale.com) (VPN privé) —
  le Jetson garde une adresse stable joignable depuis un téléphone même en
  4G, sans être sur le wifi de l'atelier.
- **Caméra** : DVR **Dahua** existant sur le réseau local de l'atelier,
  caméra 15 (canal DMSS 15), flux principal **1280x720**.
  - Direct : `rtsp://<user>:<password>@<ip>:<port>/cam/realmonitor?channel=<canal>&subtype=0`
  - Enregistrements : `rtsp://<user>:<password>@<ip>:<port>/cam/playback?channel=<canal>&subtype=0&starttime=...&endtime=...`
  - `CAMERA_CHANNEL` = numéro de canal **tel qu'affiché dans l'appli DMSS**
    (ex: `15`), pas un numéro de flux Hikvision.
  - Identifiants jamais en clair dans le code : variables d'environnement
    (fichier `.env`, non versionné — voir `.env.example`).

## Fonctionnement

1. Connexion au flux RTSP du DVR Dahua (`src/camera_stream.py`) — direct ou
   lecture d'enregistrements passés.
2. Détection de présence près de la machine, trois méthodes possibles
   (variable d'environnement `PRESENCE_MODE`, voir plus bas) :
   - **`fragments`** *(défaut)* — surveille 4 petites zones (tête, jambes, pile, convoyeur) où un morceau
     du corps du conducteur est visible malgré les obstructions de la
     machine (`src/fragment_detection.py`). Voir `NOTES-SESSION.md` pour le
     détail complet et les limites connues.
   - **`yolo`** — détection de personne entière (YOLOv8n) dans une zone
     rectangulaire (`src/detection.py`, `src/zone.py`), gardée pour
     comparaison/secours.
   - **`both`** — présence si l'une OU l'autre méthode détecte quelque
     chose.
3. Boucle principale (`main.py`) : ~1 image analysée par seconde (le flux
   continue d'être lu en continu pour éviter le retard RTSP), gestion des
   sessions avec tolérance d'absence configurable, écriture dans
   `sessions.csv`.
4. Tableau de bord web minimal (`dashboard.py`, port 8000) : liste des
   sessions + images de calibrage/débogage, consultable depuis un
   téléphone via Tailscale, sans SSH.
5. *(à venir)* Envoi du statut vers l'API `suivi-production-imprimerie`.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# éditer .env et renseigner DVR_IP, DVR_USER, DVR_PASSWORD, DVR_PORT, CAMERA_CHANNEL
```

## Lancement manuel

```bash
.venv/bin/python3 main.py       # suivi de présence -> sessions.csv
.venv/bin/python3 dashboard.py  # tableau de bord web, port 8000
```

En production, les deux tournent en continu via systemd — voir
`deploy/README.md`.

## Variables d'environnement utiles (voir `.env.example`)

| Variable | Rôle | Défaut |
|---|---|---|
| `PRESENCE_MODE` | `fragments` \| `yolo` \| `both` | `fragments` |
| `ABSENCE_TOLERANCE_SECONDS` | secondes d'absence avant de clôturer une session | `30` |
| `MACHINE_NAME` | nom affiché dans le tableau de bord et `sessions.csv` | `Machine 1` |

## Calibrer / recalibrer la détection par fragments

1. `compute_reference_fragments.py --date AAAA-MM-JJ --debut HH:MM:SS --duree 3600 --pas 30`
   — recalcule `reference_fragments.png` (médiane de plusieurs images
   espacées, efface les présences occasionnelles pour ne garder que le
   fond "machine vide").
2. `contact_sheet.py --date AAAA-MM-JJ --debut HH:MM:SS --duree 1200 --pas 10`
   — produit des **pages** de validation lisibles sur téléphone
   (`contact_pages/page_01.png`, `page_02.png`, ...), 6 vignettes par page
   (2 colonnes x 3 lignes), **recadrées en pleine résolution** sur la zone
   utile (constante `CROP` dans le script — les zones + le bout droit de
   la machine, pas toute la caméra) : chaque vignette montre l'heure, le
   statut (bandeau rouge/vert), les zones (rouge si déclenchée) et leur
   ratio (ex. `tete 0.12/0.08`). Consultable depuis le téléphone sans SSH
   via `/contact` sur le tableau de bord (voir plus bas). Les anciennes
   pages sont supprimées avant d'écrire les nouvelles.

   Sauvegarde aussi chaque image plein cadre échantillonnée dans
   `debug_frames/<AAAA-MM-JJ>_<HHMMSS-debut>/frame_<HHMMSS>.jpg` (JPEG
   qualité 92) — utile car le DVR écrase ses enregistrements au bout de
   17 jours. Pour régénérer les pages plus tard à partir de ces images
   sans redemander au DVR :
   ```bash
   .venv/bin/python3 contact_sheet.py --depuis-dossier debug_frames/2026-09-05_114000
   ```
3. `test_fragments_on_recording.py --date AAAA-MM-JJ --debut HH:MM:SS --duree 1800`
   — rejoue un enregistrement et affiche une chronologie présent/absent
   seconde par seconde avec le ratio de chaque zone, utile pour ajuster
   `ZONES` et `THRESHOLDS` dans `src/fragment_detection.py`.

Les scripts équivalents pour l'ancienne méthode (soustraction de fond sur
toute la machine) existent aussi : `capture_reference.py`,
`compute_machine_mask.py`, `test_background_on_recording.py`.

Ces trois scripts (`compute_reference_fragments.py`,
`test_fragments_on_recording.py`, `contact_sheet.py`) écrivent leur
avancement avec `flush=True` — utile pour les lancer en arrière-plan et
suivre en direct avec `tail -f` :
```bash
nohup .venv/bin/python3 contact_sheet.py --date 2026-09-05 --debut 11:40:00 --duree 1200 > contact.log 2>&1 &
tail -f contact.log
```

## Vérifier la zone convoyeur (à lancer sur le Jetson)

Référence sur une fenêtre longue (la médiane efface mieux le conducteur),
puis feuille de contact et chronologie sur la plage du 2026-09-23 :

```bash
cd ~/suivi-de-machine && git pull
.venv/bin/python3 compute_reference_fragments.py --date 2026-09-23 --debut 12:30:00 --duree 5400 --pas 30
.venv/bin/python3 contact_sheet.py --date 2026-09-23 --debut 13:15:00 --duree 900 --pas 5
.venv/bin/python3 test_fragments_on_recording.py --date 2026-09-23 --debut 13:15:00 --duree 900 > test_convoyeur.log
grep -E '^\[13:21:(0[7-9]|1[0-6])\]' test_convoyeur.log   # doit afficher PRESENT sur toute la fenêtre
grep -c PRESENT test_convoyeur.log                         # nombre de secondes "présent" sur les 15 min
sudo systemctl restart suivi-presence                      # prise en compte de la nouvelle zone
```

Les pages sont visibles depuis le téléphone sur `/contact`.

## Outils de débogage visuel (depuis le tableau de bord, port 8000)

| Chemin | Contenu |
|---|---|
| `/calibrate.png`, `/calibrate_day.png` | capture brute avec grille de coordonnées |
| `/reference_fragments.png` | référence "machine vide" (méthode fragments) |
| `/zones_debug.png` | dernière image analysée, zones dessinées (rouge = déclenchée), régénérée toutes les 10s par `main.py` |
| `/contact` | pages de validation produites par `contact_sheet.py` (voir ci-dessus), une à la fois, avec navigation précédent/suivant |
| `/reference_background.png`, `/machine_mask_preview.png` | référence et contour (ancienne méthode background_detection) |

## État d'avancement

- [x] Matériel reçu et en service (Jetson, DVR Dahua, Tailscale).
- [x] Connexion RTSP réelle (direct + lecture des enregistrements).
- [x] Services systemd (`suivi-presence`, `suivi-dashboard`) — voir `deploy/`.
- [x] Détection par fragments (méthode par défaut) — code et zones en
      place, **validation sur enregistrement réel restant à faire** (voir
      `NOTES-SESSION.md`, section « Détection par fragments »).
- [ ] Validation chiffrée (taux de bonnes détections) sur au moins 20 min
      d'enregistrement réel, et ajustement des seuils si besoin.
- [ ] Intégration de l'envoi des résultats vers l'API
      `suivi-production-imprimerie`.

Voir [`NOTES-SESSION.md`](./NOTES-SESSION.md) pour le détail des choix
techniques, l'historique des méthodes essayées, et les limites connues.
