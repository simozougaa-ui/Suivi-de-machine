"""Fonctions partagées entre les outils de ce dossier (réglage des zones de
mouvement). Indépendant de `machine_etat/` : aucun de ces outils n'envoie
quoi que ce soit à l'application de suivi, ce sont des outils de mesure
locaux.
"""

import json
import os
from datetime import timedelta

from src.camera_stream import build_rtsp_playback_url, open_stream

DOSSIER = os.path.dirname(os.path.abspath(__file__))
FICHIER_ZONES = os.path.join(DOSSIER, "zones.json")
SORTIES_DIR = os.path.join(DOSSIER, "sorties")


def charger_zones():
    """Charge les zones nommées (fichier zones.json, modifiable à la main
    entre deux essais) sous la forme {nom: (x1, y1, x2, y2)}."""
    with open(FICHIER_ZONES, encoding="utf-8") as f:
        brut = json.load(f)
    return {nom: tuple(coords) for nom, coords in brut.items()}


def capturer_image(instant, duree_lecture=10):
    """Ouvre l'enregistrement du DVR à `instant` et retourne la première
    image lue. Lève RuntimeError si le flux ne produit aucune image
    (DVR indisponible, pas d'enregistrement à cette heure, etc.)."""
    url = build_rtsp_playback_url(instant, instant + timedelta(seconds=duree_lecture))
    capture = open_stream(url)
    try:
        ret, frame = capture.read()
    finally:
        capture.release()
    if not ret or frame is None:
        raise RuntimeError(f"Impossible de lire une image à {instant:%Y-%m-%d %H:%M:%S}.")
    return frame
