"""Calcule l'image de référence "machine vide" pour la détection par
fragments (src/fragment_detection.py), en prenant la médiane pixel à
pixel de plusieurs images espacées dans le temps.

Pourquoi la médiane et pas une seule image : le conducteur n'est pas tout
le temps présent dans chaque zone. Sur suffisamment d'images espacées
dans le temps, il n'apparaît que sur une minorité d'entre elles à un
endroit donné — la médiane pixel à pixel l'efface et ne garde que le
fond stable (la machine, le sol), contrairement à une seule capture qui
pourrait le figer par hasard dans la référence.

Usage :
    .venv/bin/python3 compute_reference_fragments.py --date 2026-09-05 --debut 11:00:00 --duree 3600 --pas 30

--pas : intervalle en secondes entre deux images gardées pour la médiane
(défaut 30s). Sur une fenêtre d'une heure, ça donne 120 échantillons —
largement assez pour effacer une présence occasionnelle par zone.
"""

import argparse
from datetime import datetime, timedelta

import cv2
import numpy as np

from src.camera_stream import build_rtsp_playback_url, open_stream
from src.fragment_detection import REFERENCE_FILE

DEFAULT_STEP_SECONDS = 30
ASSUMED_FPS = 15.0  # estimation, comme dans les autres scripts de lecture d'enregistrement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debut", required=True, help="Heure de debut, format HH:MM:SS")
    parser.add_argument("--duree", type=int, default=1800, help="Duree a parcourir, en secondes (defaut 1800 = 30 min)")
    parser.add_argument("--date", default=None, help="Date, format YYYY-MM-DD (defaut: aujourd'hui)")
    parser.add_argument("--pas", type=int, default=DEFAULT_STEP_SECONDS, help="Intervalle entre deux images gardees, en secondes")
    args = parser.parse_args()

    jour = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now().date()
    heure = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(jour, heure)
    end = start + timedelta(seconds=args.duree)

    url = build_rtsp_playback_url(start, end)
    print(f"Lecture de l'enregistrement entre {start.time()} et {end.time()}, un echantillon toutes les {args.pas}s...")

    capture = open_stream(url)
    samples = []
    frame_count = 0
    next_sample_frame = 0
    step_frames = max(1, int(args.pas * ASSUMED_FPS))

    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            frame_count += 1

            if frame_count >= next_sample_frame:
                samples.append(frame)
                next_sample_frame = frame_count + step_frames
                print(f"  echantillon {len(samples)} pris (image {frame_count})")
    finally:
        capture.release()

    if len(samples) < 3:
        print(f"ECHEC: seulement {len(samples)} echantillon(s), pas assez pour une mediane fiable (minimum 3). Augmentez --duree ou reduisez --pas.")
        return

    stack = np.stack(samples, axis=0)
    median = np.median(stack, axis=0).astype(np.uint8)

    cv2.imwrite(REFERENCE_FILE, median)
    print(f"OK: reference calculee sur {len(samples)} images, sauvegardee dans {REFERENCE_FILE}")


if __name__ == "__main__":
    main()
