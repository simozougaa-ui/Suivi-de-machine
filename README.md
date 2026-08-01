# Suivi de machine — Détection de présence par caméra

## Objectif

Suivre en temps réel la présence d'un conducteur/opérateur sur une zone de
travail définie, à partir d'un flux vidéo caméra, afin de fournir une donnée
de présence ("présent" / "absent") horodatée. Ce projet est le socle qui
alimentera plus tard, via une API HTTP, le projet
[`suivi-production-imprimerie`](https://github.com/simozougaa-ui/suivi-production-imprimerie).

**Ce dépôt est indépendant** : pas de dépendance de code ni de fusion avec
`suivi-production-imprimerie`. La communication entre les deux se fera
uniquement par appels HTTP (ce boîtier enverra ses résultats de détection à
l'API de suivi de production).

## Matériel cible

- **Boîtier de calcul** : NVIDIA Jetson Orin Nano Super (non reçu à ce jour —
  développement fait à l'avance, sans matériel physique disponible).
- **Caméra** : DVR Hikvision existant sur le réseau local, flux RTSP.
  - URL type : `rtsp://<user>:<password>@<ip>:<port>/Streaming/Channels/<canal>`
  - Canal utilisé en production : `1501` (canal 15, flux principal).
  - Le mot de passe et les identifiants ne sont **jamais** stockés en clair
    dans le code : ils sont lus depuis des variables d'environnement (fichier
    `.env`, non versionné — voir `.env.example`).

## Fonctionnement prévu

1. Connexion au flux RTSP du DVR Hikvision (`src/camera_stream.py`).
2. Détection des personnes présentes dans l'image via un modèle YOLOv8n
   pré-entraîné (`src/detection.py`).
3. Vérification de l'intersection entre les détections et une zone de travail
   rectangulaire définie (`src/zone.py`).
4. Boucle principale qui affiche en console le statut "présent" / "absent"
   avec horodatage (`src/main.py`).
5. *(à venir)* Envoi du statut vers l'API `suivi-production-imprimerie` via
   `requests`.

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# éditer .env et renseigner DVR_IP, DVR_USER, DVR_PASSWORD, DVR_PORT, CAMERA_CHANNEL
```

## Lancement

```bash
python3 src/main.py
```

## État d'avancement

- [x] Structure du projet, dépendances, gestion sécurisée des identifiants.
- [x] Code de connexion RTSP, détection de personnes, gestion de zone, boucle
      principale — écrit mais **non testé en conditions réelles** (pas de
      Jetson, pas d'accès direct au flux RTSP depuis l'environnement de
      développement).
- [ ] Réception du Jetson Orin Nano Super et installation de l'environnement
      d'exécution (JetPack, dépendances GPU).
- [ ] Test réel de connexion au flux RTSP du DVR Hikvision.
- [ ] Calibrage de la zone de travail (`WORK_ZONE` dans `src/main.py`) sur une
      image réelle de la caméra.
- [ ] Intégration de l'envoi des résultats vers l'API
      `suivi-production-imprimerie`.

Voir [`NOTES-SESSION.md`](./NOTES-SESSION.md) pour le détail des choix
techniques et la suite des étapes.
