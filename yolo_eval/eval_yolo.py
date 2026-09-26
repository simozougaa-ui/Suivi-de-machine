"""Évaluation ISOLÉE d'une détection de personne YOLO sur la caméra 15.

Ne touche à aucun fichier du système en production : lit seulement
l'enregistrement du DVR (via src/camera_stream.py, importé en lecture) ou un
dossier d'images debug_frames/, et écrit ses résultats dans yolo_eval/out/.

Pour chaque image analysée (une toutes les --pas secondes) : détection des
personnes, classement de chaque boîte "machine" (dans la zone de la machine
suivie) ou "ailleurs" (autres ouvriers, passants), mesure du temps
d'inférence. Produit :
- out/<date>_<HHMMSS>/detections.csv : une ligne par image analysée
- out/<date>_<HHMMSS>/resume.txt : modèle, appareil, FPS, taux de détection
- out/<date>_<HHMMSS>/img_<HHMMSS>.jpg : images annotées (moments clés,
  premières détections sur la machine, et une image de contrôle toutes les
  --controle secondes)

Usage (depuis la racine du dépôt, avec le venv isolé) :
    yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --date 2026-09-23 --debut 13:21:00 --duree 1440
    yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --depuis-dossier debug_frames/2026-09-23_132100
Pour voir les images depuis le téléphone (Tailscale), sans toucher au
tableau de bord :
    python3 -m http.server 8001 --directory yolo_eval/out
puis http://100.116.160.30:8001/
"""

import argparse
import csv
import os
import re
import sys
import time
from datetime import datetime, timedelta

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))

# Zone de la machine suivie : union des 4 zones de fragment_detection.py
# (tête, jambes, pile, convoyeur) avec une marge. Définie ici en dur pour ne
# dépendre d'aucun fichier de production. Une personne détectée dedans est
# candidate "conducteur" ; ailleurs, c'est un autre ouvrier ou un passant.
ZONE_MACHINE = (520, 60, 830, 400)

ASSUMED_FPS = 15.0
PERSON_CLASS = 0
DEFAULT_MOMENTS = "13:21:07,13:21:10,13:21:13,13:21:16"
FRAME_RE = re.compile(r"^frame_(\d{2})(\d{2})(\d{2})\.jpg$")
MAX_IMAGES_MACHINE = 40


def in_zone(box, zone=ZONE_MACHINE):
    x1, y1, x2, y2 = box
    zx1, zy1, zx2, zy2 = zone
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    return zx1 <= cx <= zx2 and zy1 <= cy <= zy2


def frames_from_recording(jour, debut, duree, pas):
    sys.path.insert(0, ROOT)
    os.chdir(ROOT)  # pour que camera_stream trouve le .env du projet
    from src.camera_stream import build_rtsp_playback_url, open_stream

    start = datetime.combine(jour, debut)
    capture = open_stream(build_rtsp_playback_url(start, start + timedelta(seconds=duree)))
    step = max(1, int(pas * ASSUMED_FPS))
    count, next_frame = 0, 0
    try:
        while True:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            count += 1
            if count < next_frame:
                continue
            next_frame = count + step
            yield start + timedelta(seconds=count / ASSUMED_FPS), frame
    finally:
        capture.release()


def frames_from_folder(folder):
    base = os.path.basename(folder.rstrip("/"))
    jour = datetime.strptime(base.split("_")[0], "%Y-%m-%d").date()
    for name in sorted(os.listdir(folder)):
        m = FRAME_RE.match(name)
        if not m:
            continue
        h, mi, s = map(int, m.groups())
        frame = cv2.imread(os.path.join(folder, name))
        if frame is not None:
            yield datetime.combine(jour, datetime.min.time()).replace(hour=h, minute=mi, second=s), frame


def annotate(frame, boxes, timestamp):
    out = frame.copy()
    zx1, zy1, zx2, zy2 = ZONE_MACHINE
    cv2.rectangle(out, (zx1, zy1), (zx2, zy2), (255, 255, 0), 1)
    for (x1, y1, x2, y2), conf, machine in boxes:
        color = (0, 0, 255) if machine else (0, 220, 255)
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        cv2.putText(out, f"{conf:.2f}", (int(x1), max(12, int(y1) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    n_machine = sum(1 for b in boxes if b[2])
    label = f"{timestamp:%H:%M:%S}  personnes: {len(boxes)}  dont zone machine: {n_machine}"
    cv2.rectangle(out, (0, 0), (out.shape[1], 30), (0, 0, 0), -1)
    cv2.putText(out, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--debut", help="HH:MM:SS")
    ap.add_argument("--duree", type=int, default=1440)
    ap.add_argument("--pas", type=float, default=1.0, help="secondes entre deux images analysées")
    ap.add_argument("--depuis-dossier")
    ap.add_argument("--modele", default=os.path.join(HERE, "yolo11n.pt"),
                    help="yolo11n.pt (PyTorch) ou yolo11n_1280_fp16.engine (TensorRT, nécessite torch avec CUDA)")
    ap.add_argument("--imgsz", type=int, default=1280)
    ap.add_argument("--conf", type=float, default=0.30)
    ap.add_argument("--moments", default=DEFAULT_MOMENTS, help="heures HH:MM:SS à annoter, séparées par des virgules")
    ap.add_argument("--controle", type=int, default=30, help="une image annotée de contrôle toutes les N secondes")
    args = ap.parse_args()

    from ultralytics import YOLO
    import torch

    device = 0 if torch.cuda.is_available() else "cpu"
    model = YOLO(args.modele)

    if args.depuis_dossier:
        source = frames_from_folder(os.path.abspath(args.depuis_dossier))
        tag = os.path.basename(args.depuis_dossier.rstrip("/"))
    else:
        if not (args.date and args.debut):
            ap.error("--date et --debut requis (ou --depuis-dossier)")
        jour = datetime.strptime(args.date, "%Y-%m-%d").date()
        debut = datetime.strptime(args.debut, "%H:%M:%S").time()
        source = frames_from_recording(jour, debut, args.duree, args.pas)
        tag = f"{args.date}_{args.debut.replace(':', '')}"

    out_dir = os.path.join(HERE, "out", tag)
    os.makedirs(out_dir, exist_ok=True)
    moments = {m.strip() for m in args.moments.split(",") if m.strip()}

    rows, infer_times = [], []
    n_images_machine = 0
    last_control = None
    t_start = time.perf_counter()
    warmed = False

    for timestamp, frame in source:
        if not warmed:
            model.predict(frame, imgsz=args.imgsz, conf=args.conf, classes=[PERSON_CLASS], device=device, verbose=False)
            warmed = True
        t0 = time.perf_counter()
        res = model.predict(frame, imgsz=args.imgsz, conf=args.conf, classes=[PERSON_CLASS], device=device, verbose=False)[0]
        infer_times.append(time.perf_counter() - t0)

        boxes = []
        for b in res.boxes:
            xyxy = [float(v) for v in b.xyxy[0].tolist()]
            boxes.append((xyxy, float(b.conf[0]), in_zone(xyxy)))
        n_machine = sum(1 for b in boxes if b[2])
        rows.append([f"{timestamp:%H:%M:%S}", len(boxes), n_machine,
                     max((b[1] for b in boxes), default=0.0),
                     " ".join(f"{int(x1)},{int(y1)},{int(x2)},{int(y2)}:{c:.2f}{'M' if m else ''}"
                              for (x1, y1, x2, y2), c, m in boxes)])
        print(f"[{timestamp:%H:%M:%S}] personnes={len(boxes)} zone_machine={n_machine} "
              f"({infer_times[-1] * 1000:.0f} ms)", flush=True)

        hhmmss = f"{timestamp:%H:%M:%S}"
        save = hhmmss in moments
        if n_machine and n_images_machine < MAX_IMAGES_MACHINE:
            save = True
            n_images_machine += 1
        if last_control is None or (timestamp - last_control).total_seconds() >= args.controle:
            save = True
            last_control = timestamp
        if save:
            cv2.imwrite(os.path.join(out_dir, f"img_{timestamp:%H%M%S}.jpg"),
                        annotate(frame, boxes, timestamp), [cv2.IMWRITE_JPEG_QUALITY, 88])

    total = time.perf_counter() - t_start
    if not rows:
        print("ECHEC : aucune image analysée.", flush=True)
        return

    with open(os.path.join(out_dir, "detections.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["heure", "nb_personnes", "nb_zone_machine", "conf_max", "boites(x1,y1,x2,y2:conf, M=zone machine)"])
        w.writerows(rows)

    mean_ms = 1000 * sum(infer_times) / len(infer_times)
    n = len(rows)
    lines = [
        f"modele            : {os.path.basename(args.modele)}",
        f"appareil          : {'GPU (CUDA)' if device == 0 else 'CPU'}",
        f"imgsz / conf      : {args.imgsz} / {args.conf}",
        f"images analysees  : {n} (une toutes les {args.pas}s)",
        f"inference moyenne : {mean_ms:.0f} ms/image -> {1000 / mean_ms:.1f} FPS (modele seul)",
        f"bout en bout      : {n / total:.2f} images analysees/s (lecture DVR/disque comprise)",
        f"images avec >=1 personne              : {sum(1 for r in rows if r[1])}/{n}",
        f"images avec >=1 personne zone machine : {sum(1 for r in rows if r[2])}/{n}",
        f"images avec personne hors zone machine: {sum(1 for r in rows if r[1] > r[2])}/{n}",
        f"moments cles     : " + ", ".join(
            f"{m}={'OUI' if any(r[0] == m and r[2] for r in rows) else 'non'}" for m in sorted(moments)),
    ]
    with open(os.path.join(out_dir, "resume.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    print(f"Images annotees et CSV dans {out_dir}", flush=True)


if __name__ == "__main__":
    main()
