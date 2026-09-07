"""Calcule le contour affiné (détourage automatique, GrabCut) de la
machine, à partir du polygone approximatif MACHINE_POLYGON, et le
sauvegarde dans machine_mask.png.

À lancer UNE SEULE FOIS (ou à chaque fois qu'on veut recalibrer) — pas en
continu, GrabCut est trop lent pour tourner sur chaque image en direct.
Une fois machine_mask.png créé, background_detection.py le charge et
l'utilise automatiquement.

Usage :
    .venv/bin/python3 compute_machine_mask.py --date 2026-09-05 --heure 12:00:20
"""

import argparse
from datetime import datetime, timedelta

import cv2

from src.background_detection import MACHINE_MASK_FILE, refine_mask_grabcut
from src.camera_stream import build_rtsp_playback_url, open_stream


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

    print("Calcul du detourage (GrabCut, quelques secondes)...")
    mask = refine_mask_grabcut(frame)
    cv2.imwrite(MACHINE_MASK_FILE, mask)
    print(f"OK: masque sauvegarde dans {MACHINE_MASK_FILE} ({mask.shape[1]}x{mask.shape[0]})")


if __name__ == "__main__":
    main()
