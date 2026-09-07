"""Capture l'image de référence (zone vide) pour la détection par
soustraction de fond (voir src/background_detection.py).

À lancer quand personne n'est présent près de la machine, avec un
éclairage proche de celui utilisé en conditions réelles (idéalement de
jour, avant le début du travail).

Usage :
    .venv/bin/python3 capture_reference.py
        (capture en direct, maintenant)

    .venv/bin/python3 capture_reference.py --date 2026-09-05 --heure 08:00:00
        (capture sur un enregistrement passe, ex: 8h du matin, avant que
        l'atelier ne commence a tourner)
"""

import argparse
from datetime import datetime, timedelta

import cv2

from src.background_detection import REFERENCE_FILE, crop_roi
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

    roi_frame = crop_roi(frame)
    cv2.imwrite(REFERENCE_FILE, roi_frame)
    height, width = roi_frame.shape[:2]
    print(f"OK: reference sauvegardee dans {REFERENCE_FILE} ({width}x{height})")


if __name__ == "__main__":
    main()
