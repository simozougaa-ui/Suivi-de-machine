"""Extraction ISOLÉE de frames brutes DIRECTEMENT depuis le flux mainstream
réel du DVR (RTSP), sans passer par l'appli DMSS ni par un
redimensionnement/recompression manuel — contrairement aux 57 images
utilisées jusqu'ici pour yolo_eval/ et signature_eval/ (captures d'écran
DMSS, doublement compressées, voir NOTES-SESSION.md).

Réutilise telle quelle la méthode déjà en place dans le projet pour lire
les enregistrements (src/camera_stream.build_rtsp_playback_url +
open_stream), donc le même flux (subtype=0, mainstream) que la production.
Ne modifie aucun fichier de production, ni yolo_eval/eval_yolo.py, ni
signature_eval/signature.py : ce script produit seulement un dossier de
frames que ces deux scripts peuvent consommer sans changement
(--depuis-dossier pour eval_yolo.py, --frames-dir pour signature.py).

Format de sortie : `frame_HHMMSS.jpg`, qualité JPEG 95 (perte minimale,
juste ce qu'il faut pour un fichier exploitable — pas de retraitement),
résolution NATIVE du flux décodé (aucun cv2.resize).

Usage :
    python3 signature_eval/extract_frames_dvr.py \
        --date 2026-09-23 --debut 13:21:00 --duree 1500 --pas 1 \
        --out-dir signature_eval/frames_dvr_2026-09-23_132100
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSUMED_FPS = 15.0  # mainstream confirmé 1280x720 H.264 15fps (dvr_check/)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="AAAA-MM-JJ")
    ap.add_argument("--debut", required=True, help="HH:MM:SS")
    ap.add_argument("--duree", type=int, default=1500, help="secondes")
    ap.add_argument("--pas", type=float, default=1.0, help="secondes entre deux frames sauvegardées")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()

    sys.path.insert(0, ROOT)
    os.chdir(ROOT)
    from src.camera_stream import build_rtsp_playback_url, open_stream

    os.makedirs(args.out_dir, exist_ok=True)
    jour = datetime.strptime(args.date, "%Y-%m-%d").date()
    debut = datetime.strptime(args.debut, "%H:%M:%S").time()
    start = datetime.combine(jour, debut)
    capture = open_stream(build_rtsp_playback_url(start, start + timedelta(seconds=args.duree)))

    step = max(1, int(args.pas * ASSUMED_FPS))
    count, next_frame, n_saved = 0, 0, 0
    w = h = None
    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            count += 1
            if count < next_frame:
                continue
            next_frame = count + step
            if w is None:
                h, w = frame.shape[:2]
            ts = start + timedelta(seconds=count / ASSUMED_FPS)
            name = f"frame_{ts:%H%M%S}.jpg"
            cv2.imwrite(os.path.join(args.out_dir, name), frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
            n_saved += 1
            print(f"[{ts:%H:%M:%S}] {name} sauvegardee", flush=True)
    finally:
        capture.release()

    print(f"\n{n_saved} frames sauvegardees dans {args.out_dir}", flush=True)
    print(f"Resolution native lue depuis le flux mainstream : {w}x{h}", flush=True)
    if w and (w, h) != (1280, 720):
        print("ATTENTION : resolution differente de 1280x720 attendue pour le mainstream "
              "(voir dvr_check/) - verifier la config du DVR.", flush=True)


if __name__ == "__main__":
    main()
