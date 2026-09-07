"""Montre uniquement l'intérieur du contour de la machine (MACHINE_POLYGON),
tout le reste mis en blanc — pour vérifier visuellement que le contour
suit bien la machine et rien d'autre autour.

Usage :
    .venv/bin/python3 preview_machine_mask.py --date 2026-09-05 --heure 12:00:20
"""

import argparse
from datetime import datetime, timedelta

import cv2
import numpy as np

from src.background_detection import _MASK, _ROI, crop_roi
from src.camera_stream import build_rtsp_playback_url, open_stream

OUTPUT_PATH = "machine_mask_preview.png"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--heure", default=None, help="Heure a lire, format HH:MM:SS (sinon: direct)")
    parser.add_argument("--date", default=None, help="Date a lire, format YYYY-MM-DD (avec --heure)")
    args = parser.parse_args()

    if args.heure:
        jour = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now().date()
        heure = datetime.strptime(args.heure, "%H:%M:%S").time()
        start = datetime.combine(jour, heure)
        end = start + timedelta(seconds=10)
        url = build_rtsp_playback_url(start, end)
        print(f"Lecture de l'enregistrement a {start}...")
    else:
        url = None
        print("Lecture du flux en direct...")

    capture = open_stream(url)
    ret, frame = capture.read()
    capture.release()

    if not ret or frame is None:
        print("ECHEC: impossible de lire une image.")
        return

    roi_frame = crop_roi(frame, _ROI)
    machine_only = cv2.bitwise_and(roi_frame, roi_frame, mask=_MASK)
    white_background = np.full_like(roi_frame, 255)
    inverse_mask = cv2.bitwise_not(_MASK)
    background_only = cv2.bitwise_and(white_background, white_background, mask=inverse_mask)
    masked = cv2.add(machine_only, background_only)

    cv2.imwrite(OUTPUT_PATH, masked)
    height, width = masked.shape[:2]
    print(f"OK: apercu sauvegarde dans {OUTPUT_PATH} ({width}x{height})")


if __name__ == "__main__":
    main()
