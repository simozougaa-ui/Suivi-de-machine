"""Détection de présence par soustraction de fond, pour une zone où la
caméra ne voit la machine que de côté/de dos (le YOLO de détection de
personnes entières ne fonctionne pas bien si seule une partie du corps
est visible, cachée par la machine elle-même).

Principe : on capture une image de référence de la zone **vide** (aucune
activité), puis on compare chaque nouvelle image à cette référence. Le
moindre changement de pixels (un bout de bras, une ombre, un objet) dans
cette zone est considéré comme une présence — pas besoin de reconnaître
une personne entière.

Limite à connaître : la référence doit être reprise si l'éclairage change
fortement (ex: jour → nuit, mode infrarouge). Une référence prise de jour
ne sert à rien la nuit.
"""

import cv2
import numpy as np

# Rectangle englobant la machine tracée par l'utilisateur (voir la photo
# annotée du 2026-09-05 12:00:20) : coin haut-gauche à bas-droit.
MACHINE_ROI = (180, 80, 700, 400)  # (x1, y1, x2, y2) — trace precis du 2026-09-05 12:00:20

REFERENCE_FILE = "reference_background.png"
DIFF_THRESHOLD = 30  # écart de niveau de gris à partir duquel un pixel compte comme "change"
CHANGE_RATIO_THRESHOLD = 0.02  # fraction de la zone qui doit changer pour dire "present"
BLUR_KERNEL = (5, 5)  # attenue le bruit de l'image (grain de compression, reflets)


def crop_roi(frame, roi=MACHINE_ROI):
    x1, y1, x2, y2 = roi
    return frame[y1:y2, x1:x2]


def _prepare(frame_roi):
    gray = cv2.cvtColor(frame_roi, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, BLUR_KERNEL, 0)


def load_reference(path=REFERENCE_FILE):
    """Charge l'image de référence (déjà recadrée sur MACHINE_ROI)."""
    reference = cv2.imread(path)
    if reference is None:
        raise FileNotFoundError(
            f"Image de reference introuvable : {path}. "
            "Lancez capture_reference.py quand la zone est vide."
        )
    return _prepare(reference)


def change_ratio(frame, reference_prepared, roi=MACHINE_ROI):
    """Retourne la fraction de pixels de la zone qui diffèrent de la référence."""
    current = _prepare(crop_roi(frame, roi))
    if current.shape != reference_prepared.shape:
        raise ValueError(
            "L'image actuelle et la reference n'ont pas la meme taille "
            f"({current.shape} vs {reference_prepared.shape}) — "
            "la reference a-t-elle ete capturee avec la meme resolution ?"
        )

    diff = cv2.absdiff(current, reference_prepared)
    _, mask = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    changed_pixels = cv2.countNonZero(mask)
    total_pixels = mask.shape[0] * mask.shape[1]
    return changed_pixels / total_pixels


def is_present(frame, reference_prepared, roi=MACHINE_ROI, ratio_threshold=CHANGE_RATIO_THRESHOLD):
    return change_ratio(frame, reference_prepared, roi) > ratio_threshold
