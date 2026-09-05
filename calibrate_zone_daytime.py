"""Comme calibrate_zone.py, mais lit un enregistrement du DVR (pas le direct)
à une heure de la journée, pour avoir une image plus claire qu'en pleine
nuit (mode vision nocturne infrarouge, granuleux).

Usage :
    .venv/bin/python3 calibrate_zone_daytime.py
    .venv/bin/python3 calibrate_zone_daytime.py --heure 14:00:00

Par défaut, lit un extrait de 10 secondes à 13h00 le jour même. Change
l'heure avec --heure si besoin (ex: pour viser une heure où l'atelier est
bien éclairé).
"""

import argparse
from datetime import datetime, timedelta

import cv2

from src.camera_stream import build_rtsp_playback_url, open_stream

OUTPUT_PATH = "calibrate_day.png"
GRID_STEP = 100
GRID_COLOR = (0, 255, 0)


def draw_grid(frame):
    height, width = frame.shape[:2]
    for x in range(0, width, GRID_STEP):
        cv2.line(frame, (x, 0), (x, height), GRID_COLOR, 1)
        cv2.putText(frame, str(x), (x + 2, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, GRID_COLOR, 1)
    for y in range(0, height, GRID_STEP):
        cv2.line(frame, (0, y), (width, y), GRID_COLOR, 1)
        cv2.putText(frame, str(y), (2, y + 15), cv2.FONT_HERSHEY_SIMPLEX, 0.4, GRID_COLOR, 1)
    return frame


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--heure", default="13:00:00", help="Heure a lire, format HH:MM:SS (jour meme)")
    parser.add_argument("--duree", type=int, default=10, help="Duree de la fenetre lue, en secondes")
    args = parser.parse_args()

    today = datetime.now().date()
    heure = datetime.strptime(args.heure, "%H:%M:%S").time()
    start = datetime.combine(today, heure)
    end = start + timedelta(seconds=args.duree)

    url = build_rtsp_playback_url(start, end)
    print(f"Lecture de l'enregistrement entre {start} et {end}...")

    capture = open_stream(url)
    ret, frame = capture.read()
    capture.release()

    if not ret or frame is None:
        print(
            "ECHEC: impossible de lire une image a cette heure. "
            "Le DVR n'a peut-etre pas d'enregistrement sur ce creneau, "
            "ou le format d'URL de lecture n'est pas celui attendu par ce DVR."
        )
        return

    frame = draw_grid(frame)
    cv2.imwrite(OUTPUT_PATH, frame)
    height, width = frame.shape[:2]
    print(f"OK: image sauvegardee dans {OUTPUT_PATH} ({width}x{height})")


if __name__ == "__main__":
    main()
