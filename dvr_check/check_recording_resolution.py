"""Vérification ISOLÉE, lecture seule : résolution réelle d'un enregistrement
déjà téléchargé (ou d'une image extraite d'un enregistrement) pour la
caméra 15, à comparer aux résolutions mainstream/substream renvoyées par
check_stream_config.sh.

Ne modifie aucun fichier de production, yolo_eval/ ou signature_eval/ : ce
script se contente de lire une image ou une courte capture RTSP et
d'afficher sa résolution.

Usage :
    # Sur une image déjà enregistrée (ex. reference_fragments.png, ou une
    # image de debug_frames/) :
    python3 dvr_check/check_recording_resolution.py --image reference_fragments.png

    # Ou directement sur le flux de lecture des enregistrements du DVR
    # (depuis le Jetson, dans la fenêtre disponible) :
    python3 dvr_check/check_recording_resolution.py \
        --date 2026-09-23 --debut 13:21:00 --duree 5
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="chemin d'une image déjà extraite d'un enregistrement")
    ap.add_argument("--date", help="AAAA-MM-JJ (lecture directe des enregistrements du DVR)")
    ap.add_argument("--debut", help="HH:MM:SS")
    ap.add_argument("--duree", type=int, default=5, help="secondes")
    args = ap.parse_args()

    if args.image:
        frame = cv2.imread(args.image)
        if frame is None:
            sys.exit(f"Image illisible : {args.image}")
        h, w = frame.shape[:2]
        print(f"{args.image} : {w}x{h}")
        return

    if not (args.date and args.debut):
        sys.exit("Fournir --image, ou --date et --debut (lecture RTSP)")

    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from src.camera_stream import build_rtsp_playback_url, open_stream

    jour = datetime.strptime(args.date, "%Y-%m-%d").date()
    debut = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(jour, debut)
    url = build_rtsp_playback_url(start, start + timedelta(seconds=args.duree))
    capture = open_stream(url)
    try:
        ret, frame = capture.read()
        if not ret or frame is None:
            sys.exit("Aucune image lue depuis l'enregistrement (fenêtre vide ou hors plage ?)")
        h, w = frame.shape[:2]
        print(f"Enregistrement {args.date} {args.debut} (subtype=0, tel que demandé par le code "
              f"de production) : {w}x{h}")
    finally:
        capture.release()


if __name__ == "__main__":
    main()
