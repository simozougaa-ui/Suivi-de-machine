"""Capture l'image de référence (zone vide) pour la détection par
soustraction de fond (voir src/background_detection.py).

À lancer quand personne n'est présent près de la machine — de préférence
en direct (le flux temps réel), à une heure où l'atelier est calme.

Usage :
    .venv/bin/python3 capture_reference.py
"""

import cv2

from src.background_detection import MACHINE_ROI, REFERENCE_FILE, crop_roi
from src.camera_stream import open_stream


def main():
    capture = open_stream()
    ret, frame = capture.read()
    capture.release()

    if not ret or frame is None:
        print("ECHEC: impossible de lire une image du flux.")
        return

    roi_frame = crop_roi(frame, MACHINE_ROI)
    cv2.imwrite(REFERENCE_FILE, roi_frame)
    height, width = roi_frame.shape[:2]
    print(f"OK: reference sauvegardee dans {REFERENCE_FILE} ({width}x{height})")


if __name__ == "__main__":
    main()
