"""Teste la détection par soustraction de fond (src/background_detection.py)
sur un enregistrement du DVR, avec la même logique de session/tolérance
que main.py.

Nécessite d'avoir déjà lancé capture_reference.py pour créer
reference_background.png.

Usage :
    .venv/bin/python3 test_background_on_recording.py --date 2026-09-05 --debut 11:40:00 --duree 1800
"""

import argparse
import csv
import os
from datetime import datetime, timedelta

from src.background_detection import is_present, load_reference
from src.camera_stream import build_rtsp_playback_url, open_stream

OUTPUT_FILE = "sessions_test_background.csv"
ABSENCE_TOLERANCE_SECONDS = 8


def log_session(start, end):
    duration = (end - start).total_seconds()
    file_exists = os.path.isfile(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["debut", "fin", "duree_secondes"])
        writer.writerow([start.isoformat(), end.isoformat(), round(duration, 1)])
    print(f"  -> session: {start.strftime('%H:%M:%S')} a {end.strftime('%H:%M:%S')} ({duration:.1f}s)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debut", required=True, help="Heure de debut, format HH:MM:SS")
    parser.add_argument("--duree", type=int, default=600, help="Duree a rejouer, en secondes")
    parser.add_argument("--date", default=None, help="Date a lire, format YYYY-MM-DD (defaut: aujourd'hui)")
    args = parser.parse_args()

    jour = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now().date()
    heure = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(jour, heure)
    end = start + timedelta(seconds=args.duree)

    reference = load_reference()

    url = build_rtsp_playback_url(start, end)
    print(f"Lecture de l'enregistrement entre {start.time()} et {end.time()}...")

    capture = open_stream(url)
    session_start = None
    last_present_time = None
    frame_count = 0

    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            frame_count += 1

            elapsed = frame_count / 15.0  # estimation ~15 im/s
            timestamp = start + timedelta(seconds=elapsed)

            try:
                present = is_present(frame, reference)
            except Exception as exc:
                print(f"Erreur detection: {exc}")
                continue

            if present:
                if session_start is None:
                    session_start = timestamp
                    print(f"[{timestamp.strftime('%H:%M:%S')}] debut de presence")
                last_present_time = timestamp
            elif session_start is not None:
                absence = (timestamp - last_present_time).total_seconds()
                if absence > ABSENCE_TOLERANCE_SECONDS:
                    log_session(session_start, last_present_time)
                    session_start = None
                    last_present_time = None

    finally:
        capture.release()

    print(f"Termine. {frame_count} images lues. Resultats dans {OUTPUT_FILE}.")


if __name__ == "__main__":
    main()
