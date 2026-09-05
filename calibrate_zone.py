"""Capture une image réelle de la caméra avec une grille de coordonnées,
pour déterminer les pixels de la zone de travail (WORK_ZONE dans main.py)
sans écran ni souris — juste en lisant l'image depuis le téléphone.

Usage :
    .venv/bin/python3 calibrate_zone.py

Puis ouvrir http://<ip-jetson>:8000/calibrate.png (le service
suivi-dashboard doit tourner) et lire les coordonnées des coins de la zone
de travail sur la grille, avant de les reporter dans WORK_ZONE.
"""

import cv2

from src.camera_stream import open_stream

OUTPUT_PATH = "calibrate.png"
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
    capture = open_stream()
    ret, frame = capture.read()
    capture.release()

    if not ret or frame is None:
        print("ECHEC: impossible de lire une image du flux RTSP.")
        return

    frame = draw_grid(frame)
    cv2.imwrite(OUTPUT_PATH, frame)
    height, width = frame.shape[:2]
    print(f"OK: image sauvegardee dans {OUTPUT_PATH} ({width}x{height})")


if __name__ == "__main__":
    main()
