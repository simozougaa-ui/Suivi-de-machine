"""Teste la détection par fragments (src/fragment_detection.py) sur un
enregistrement du DVR, avec la même logique de session/tolérance que
main.py en mode PRESENCE_MODE=fragments. Affiche une chronologie
présent/absent par seconde, avec le ratio de chaque zone.

Nécessite d'avoir déjà lancé compute_reference_fragments.py pour créer
reference_fragments.png.

Usage :
    .venv/bin/python3 test_fragments_on_recording.py --date 2026-09-05 --debut 11:40:00 --duree 1800
"""

import argparse
import csv
import os
from datetime import datetime, timedelta

from src.camera_stream import build_rtsp_playback_url, open_stream
from src.fragment_detection import FragmentPresenceTracker, load_reference

OUTPUT_FILE = "sessions_test_fragments.csv"
ABSENCE_TOLERANCE_SECONDS = 30
ASSUMED_FPS = 15.0


def log_session(start, end):
    duration = (end - start).total_seconds()
    file_exists = os.path.isfile(OUTPUT_FILE)
    with open(OUTPUT_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["debut", "fin", "duree_secondes"])
        writer.writerow([start.isoformat(), end.isoformat(), round(duration, 1)])
    print(f"  -> session: {start.strftime('%H:%M:%S')} a {end.strftime('%H:%M:%S')} ({duration:.1f}s)", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--debut", required=True, help="Heure de debut, format HH:MM:SS")
    parser.add_argument("--duree", type=int, default=600, help="Duree a rejouer, en secondes")
    parser.add_argument("--date", default=None, help="Date, format YYYY-MM-DD (defaut: aujourd'hui)")
    args = parser.parse_args()

    jour = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now().date()
    heure = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(jour, heure)
    end = start + timedelta(seconds=args.duree)

    reference = load_reference()
    tracker = FragmentPresenceTracker()

    url = build_rtsp_playback_url(start, end)
    print(f"Lecture de l'enregistrement entre {start.time()} et {end.time()}...", flush=True)

    capture = open_stream(url)
    session_start = None
    last_present_time = None
    frame_count = 0
    last_second_evaluated = -1

    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            frame_count += 1

            # On n'analyse qu'environ 1 image par seconde (comme main.py),
            # mais on continue de lire chaque image du flux pour ne pas
            # accumuler de retard sur la lecture RTSP.
            elapsed = frame_count / ASSUMED_FPS
            current_second = int(elapsed)
            if current_second == last_second_evaluated:
                continue
            last_second_evaluated = current_second

            timestamp = start + timedelta(seconds=elapsed)

            try:
                present, zone_results = tracker.update(frame, reference, timestamp)
            except Exception as exc:
                print(f"Erreur detection: {exc}", flush=True)
                continue

            zone_summary = " ".join(
                f"{name}={ratio:.2f}{'*' if triggered else ('~' if name in tracker.zones_en_attente else ' ')}"
                for name, (ratio, triggered) in zone_results.items()
            )
            print(f"[{timestamp.strftime('%H:%M:%S')}] {'PRESENT' if present else 'absent '} {zone_summary}", flush=True)

            if present:
                if session_start is None:
                    session_start = timestamp
                last_present_time = timestamp
            elif session_start is not None:
                absence = (timestamp - last_present_time).total_seconds()
                if absence > ABSENCE_TOLERANCE_SECONDS:
                    log_session(session_start, last_present_time)
                    session_start = None
                    last_present_time = None

    finally:
        capture.release()

    print(f"Termine. {frame_count} images lues. Resultats dans {OUTPUT_FILE}.", flush=True)


if __name__ == "__main__":
    main()
