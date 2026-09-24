"""Produit une feuille de contact (planche de vignettes) pour valider
visuellement la détection par fragments sur un enregistrement réel : une
vignette toutes les 10s, avec le statut prédit (PRESENT/absent) et les 3
zones dessinées dessus (rouge = déclenchée, vert = au repos).

À regarder soi-même pour vérifier que la détection correspond à la
réalité (voir NOTES-SESSION.md pour la procédure), et ajuster
ZONES/THRESHOLDS dans src/fragment_detection.py si besoin.

Usage :
    .venv/bin/python3 contact_sheet.py --date 2026-09-05 --debut 11:40:00 --duree 1200
"""

import argparse
from datetime import datetime, timedelta

import cv2
import numpy as np

from src.camera_stream import build_rtsp_playback_url, open_stream
from src.fragment_detection import FragmentPresenceTracker, ZONES, load_reference

OUTPUT_PATH = "contact_sheet.png"
SAMPLE_INTERVAL_SECONDS = 10
ASSUMED_FPS = 15.0
THUMB_WIDTH = 320
COLUMNS = 6


def annotate(frame, present, zone_results, timestamp):
    annotated = frame.copy()
    for name, (x1, y1, x2, y2) in ZONES.items():
        _, triggered = zone_results.get(name, (0.0, False))
        color = (0, 0, 255) if triggered else (0, 200, 0)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

    banner_color = (0, 0, 255) if present else (0, 150, 0)
    cv2.rectangle(annotated, (0, 0), (annotated.shape[1], 30), banner_color, -1)
    label = f"{timestamp.strftime('%H:%M:%S')} {'PRESENT' if present else 'absent'}"
    cv2.putText(annotated, label, (5, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return annotated


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debut", required=True, help="Heure de debut, format HH:MM:SS")
    parser.add_argument("--duree", type=int, default=1200, help="Duree a couvrir, en secondes (defaut 1200 = 20 min)")
    parser.add_argument("--date", default=None, help="Date, format YYYY-MM-DD (defaut: aujourd'hui)")
    args = parser.parse_args()

    jour = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now().date()
    heure = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(jour, heure)
    end = start + timedelta(seconds=args.duree)

    reference = load_reference()
    tracker = FragmentPresenceTracker()

    url = build_rtsp_playback_url(start, end)
    print(f"Lecture de l'enregistrement entre {start.time()} et {end.time()}...")
    capture = open_stream(url)

    thumbnails = []
    frame_count = 0
    next_sample_frame = 0
    step_frames = max(1, int(SAMPLE_INTERVAL_SECONDS * ASSUMED_FPS))

    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            frame_count += 1

            if frame_count < next_sample_frame:
                continue
            next_sample_frame = frame_count + step_frames

            elapsed = frame_count / ASSUMED_FPS
            timestamp = start + timedelta(seconds=elapsed)

            try:
                present, zone_results = tracker.update(frame, reference)
            except Exception as exc:
                print(f"Erreur detection: {exc}")
                continue

            annotated = annotate(frame, present, zone_results, timestamp)
            scale = THUMB_WIDTH / annotated.shape[1]
            thumb = cv2.resize(annotated, (THUMB_WIDTH, int(annotated.shape[0] * scale)))
            thumbnails.append(thumb)
            print(f"  vignette {len(thumbnails)} : {timestamp.strftime('%H:%M:%S')} {'PRESENT' if present else 'absent'}")
    finally:
        capture.release()

    if not thumbnails:
        print("ECHEC: aucune vignette produite.")
        return

    thumb_h, thumb_w = thumbnails[0].shape[:2]
    rows = (len(thumbnails) + COLUMNS - 1) // COLUMNS
    sheet = np.full((rows * thumb_h, COLUMNS * thumb_w, 3), 40, dtype=np.uint8)
    for i, thumb in enumerate(thumbnails):
        r, c = divmod(i, COLUMNS)
        sheet[r * thumb_h:(r + 1) * thumb_h, c * thumb_w:(c + 1) * thumb_w] = thumb

    cv2.imwrite(OUTPUT_PATH, sheet)
    print(f"OK: feuille de contact ({len(thumbnails)} vignettes) sauvegardee dans {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
