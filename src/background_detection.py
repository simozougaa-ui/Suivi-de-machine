"""Détection de présence par soustraction de fond, pour une zone où la
caméra ne voit la machine que de côté/de dos (le YOLO de détection de
personnes entières ne fonctionne pas bien si seule une partie du corps
est visible, cachée par la machine elle-même).

Principe : on capture une image de référence de la machine **vide**
(aucune activité), puis on compare chaque nouvelle image à cette
référence — mais seulement à l'intérieur du contour précis de la machine
(un polygone, pas un simple rectangle), pour ignorer tout ce qui bouge
autour (sol, autres personnes plus loin). Le moindre changement de pixels
à l'intérieur de ce contour (un bout de bras, une ombre) est considéré
comme une présence — pas besoin de reconnaître une personne entière.

Limite à connaître : la référence doit être reprise si l'éclairage change
fortement (ex: jour → nuit, mode infrarouge), ou si la machine elle-même
a un aspect différent à l'arrêt et en marche (pièces mobiles) — dans ce
dernier cas, une référence prise machine à l'arrêt peut détecter une
"présence" continue à cause du mouvement de la machine elle-même, pas
d'une personne.
"""

import cv2
import numpy as np

# Contour precis de la machine, releve sur calibrate_day.png du
# 2026-09-05 12:00:20 (grille + trace de l'utilisateur, corrige point par
# point). Liste de points (x, y) dans le repere de l'image complete.
MACHINE_POLYGON = [
    (200, 140),
    (280, 110),
    (420, 100),
    (550, 95),
    (650, 90),
    (700, 130),
    (700, 350),
    (630, 405),
    (450, 410),
    (280, 400),
    (180, 320),
    (180, 200),
]

REFERENCE_FILE = "reference_background.png"
DIFF_THRESHOLD = 30  # écart de niveau de gris à partir duquel un pixel compte comme "change"
CHANGE_RATIO_THRESHOLD = 0.02  # fraction de la zone qui doit changer pour dire "present"
BLUR_KERNEL = (5, 5)  # attenue le bruit de l'image (grain de compression, reflets)


def _bounding_rect(polygon):
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    return min(xs), min(ys), max(xs), max(ys)


def _build_mask(shape, polygon, origin):
    """Masque binaire (dans le repere du rectangle englobant) : 255 a
    l'interieur du polygone, 0 ailleurs."""
    ox, oy = origin
    shifted = np.array([[x - ox, y - oy] for x, y in polygon], dtype=np.int32)
    mask = np.zeros(shape, dtype=np.uint8)
    cv2.fillPoly(mask, [shifted], 255)
    return mask


MACHINE_MASK_FILE = "machine_mask.png"

_ROI = _bounding_rect(MACHINE_POLYGON)
_X1, _Y1, _X2, _Y2 = _ROI


def _load_or_build_mask():
    """Charge le masque affine (voir compute_machine_mask.py) s'il existe
    deja, sinon retombe sur le polygone brut (lignes droites)."""
    saved = cv2.imread(MACHINE_MASK_FILE, cv2.IMREAD_GRAYSCALE)
    if saved is not None:
        return saved
    return _build_mask((_Y2 - _Y1, _X2 - _X1), MACHINE_POLYGON, (_X1, _Y1))


def refine_mask_grabcut(frame, polygon=MACHINE_POLYGON, iterations=5):
    """Affine le polygone brut en un contour lisse suivant les vrais bords
    de l'objet (couleur/texture), via l'algorithme GrabCut d'OpenCV.

    A lancer une seule fois (voir compute_machine_mask.py), pas a chaque
    image en direct — GrabCut est trop lent pour tourner en continu.
    """
    poly_mask = np.zeros(frame.shape[:2], dtype=np.uint8)
    cv2.fillPoly(poly_mask, [np.array(polygon, dtype=np.int32)], 255)

    kernel = np.ones((15, 15), np.uint8)
    sure_fg = cv2.erode(poly_mask, kernel, iterations=2)
    probable_area = cv2.dilate(poly_mask, kernel, iterations=3)

    gc_mask = np.full(frame.shape[:2], cv2.GC_BGD, dtype=np.uint8)
    gc_mask[probable_area > 0] = cv2.GC_PR_BGD
    gc_mask[poly_mask > 0] = cv2.GC_PR_FGD
    gc_mask[sure_fg > 0] = cv2.GC_FGD

    bgd_model = np.zeros((1, 65), dtype=np.float64)
    fgd_model = np.zeros((1, 65), dtype=np.float64)
    cv2.grabCut(frame, gc_mask, None, bgd_model, fgd_model, iterations, cv2.GC_INIT_WITH_MASK)

    refined = np.where((gc_mask == cv2.GC_FGD) | (gc_mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    x1, y1, x2, y2 = _bounding_rect(polygon)
    return refined[y1:y2, x1:x2]


_MASK = _load_or_build_mask()
_MASK_AREA = cv2.countNonZero(_MASK)


def crop_roi(frame, roi=_ROI):
    x1, y1, x2, y2 = roi
    return frame[y1:y2, x1:x2]


def _prepare(frame_roi):
    gray = cv2.cvtColor(frame_roi, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, BLUR_KERNEL, 0)


def load_reference(path=REFERENCE_FILE):
    """Charge l'image de référence (déjà recadrée sur le rectangle englobant)."""
    reference = cv2.imread(path)
    if reference is None:
        raise FileNotFoundError(
            f"Image de reference introuvable : {path}. "
            "Lancez capture_reference.py quand la zone est vide."
        )
    return _prepare(reference)


def change_ratio(frame, reference_prepared, roi=_ROI, mask=_MASK, mask_area=_MASK_AREA):
    """Retourne la fraction de pixels **à l'intérieur du contour de la
    machine** qui diffèrent de la référence (le reste de l'image, hors
    contour, est ignoré)."""
    current = _prepare(crop_roi(frame, roi))
    if current.shape != reference_prepared.shape:
        raise ValueError(
            "L'image actuelle et la reference n'ont pas la meme taille "
            f"({current.shape} vs {reference_prepared.shape}) — "
            "la reference a-t-elle ete capturee avec la meme resolution ?"
        )

    diff = cv2.absdiff(current, reference_prepared)
    _, changed = cv2.threshold(diff, DIFF_THRESHOLD, 255, cv2.THRESH_BINARY)
    changed_inside = cv2.bitwise_and(changed, mask)
    changed_pixels = cv2.countNonZero(changed_inside)
    return changed_pixels / mask_area


def is_present(frame, reference_prepared, ratio_threshold=CHANGE_RATIO_THRESHOLD):
    return change_ratio(frame, reference_prepared) > ratio_threshold
