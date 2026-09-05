"""Teste WORK_ZONE et la détection sur un enregistrement du DVR (pas le
direct), pour vérifier la calibration sans attendre un vrai chargement en
direct.

Rejoue une plage horaire des enregistrements, applique exactement la même
logique de présence/session que main.py, et affiche/enregistre les
sessions détectées séparément (sessions_test.csv), sans toucher au fichier
de production (sessions.csv).

Usage :
    .venv/bin/python3 test_zone_on_recording.py --debut 13:00:00 --duree 600
"""

import argparse
import csv
import os
from datetime import datetime, timedelta

from src.camera_stream import build_rtsp_playback_url, open_stream
from src.detection import detect_persons
from src.zone import Zone

WORK_ZONE = Zone(x1=480, y1=0, x2=900, y2=300)
OUTPUT_FILE = "sessions_test.csv"
ABSENCE_TOLERANCE_SECONDS = 8


def check_presence(frame, zone):
    persons = detect_persons(frame)
    return any(zone.intersects_bbox(person["bbox"]) for person in persons)


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
    parser.add_argument("--debut", required=True, help="Heure de debut, format HH:MM:SS (jour meme)")
    parser.add_argument("--duree", type=int, default=600, help="Duree a rejouer, en secondes (defaut 600 = 10 min)")
    args = parser.parse_args()

    today = datetime.now().date()
    heure = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(today, heure)
    end = start + timedelta(seconds=args.duree)

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

            # Fenetre video du DVR : horodatage estime a partir du debut de
            # lecture (le flux ne donne pas l'heure exacte de chaque image).
            elapsed = frame_count / 15.0  # estimation ~15 im/s
            timestamp = start + timedelta(seconds=elapsed)

            try:
                present = check_presence(frame, WORK_ZONE)
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
