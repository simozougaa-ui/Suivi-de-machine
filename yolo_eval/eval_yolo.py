"""Évaluation ISOLÉE d'une détection de personne YOLO sur la caméra 15.

Ne touche à aucun fichier du système en production : lit seulement
l'enregistrement du DVR (via src/camera_stream.py, importé en lecture) ou un
dossier d'images debug_frames/, et écrit ses résultats dans yolo_eval/out/.

Pour chaque image analysée (une toutes les --pas secondes) : détection des
personnes, classement de chaque boîte "machine" (dans la zone de la machine
suivie) ou "ailleurs" (autres ouvriers, passants), et en plus (mise à jour
du 2026-09-27) classement spécifique "zone convoyeur" (même rectangle que
signature_eval/signature.py, ZONE_CONVOYEUR — dupliqué ici en dur, comme
partout ailleurs dans ce dossier, pour ne dépendre d'aucun autre fichier),
mesure du temps d'inférence. Produit :
- out/<date>_<HHMMSS>/detections.csv : une ligne par image analysée, avec
  nb_zone_convoyeur et presence_convoyeur (oui/non) en plus des colonnes
  existantes (nb_zone_machine reste l'union des 4 zones, inchangée)
- out/<date>_<HHMMSS>/resume.txt : modèle, appareil, FPS, taux de détection
  (dont un taux spécifique zone convoyeur)
- out/<date>_<HHMMSS>/img_<HHMMSS>.jpg : images annotées (moments clés,
  premières détections sur la machine, et une image de contrôle toutes les
  --controle secondes) ; la zone convoyeur est dessinée en plus de la zone
  machine, en magenta

Usage (depuis la racine du dépôt, avec le venv isolé) :
    yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --date 2026-09-23 --debut 13:21:00 --duree 1440
    yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --depuis-dossier debug_frames/2026-09-23_132100
Pour voir les images depuis le téléphone (Tailscale), sans toucher au
tableau de bord :
    python3 -m http.server 8001 --directory yolo_eval/out
puis http://100.116.160.30:8001/

Diagnostic "0 détection au convoyeur" (mise à jour du 2026-09-27, voir
NOTES-SESSION.md, section « Diagnostic 0/57 zone convoyeur ») : le
résultat 0/57 pouvait venir soit d'un vrai échec du modèle, soit de deux
biais du test lui-même (seuil de confiance trop haut, ou boîte détectée
recoupant la zone sans que son CENTRE n'y tombe - le test de zone
utilisé, `in_zone`, ne regarde que le centre). `--diag-convoyeur` ajoute
une seconde passe d'inférence à très bas seuil (`--diag-conf`, 0.01 par
défaut) pour capturer TOUTES les détections "person" candidates, mesure
leur chevauchement avec la zone convoyeur (IoU et % de la boîte
recouvert, pas seulement centre-dans-zone), et écrit
`out/<tag>/diagnostic_convoyeur.csv` + `diagnostic_convoyeur_resume.txt`
(distribution des scores, cas de recoupement sans centre-dans-zone).
`--verite-terrain <fichier>` (un nom de fichier par ligne) restreint la
partie "distribution des scores" aux images où la présence a été
confirmée à l'œil, pour ne pas noyer le signal dans les 42+ images vides.
`--marge-convoyeur-px` (0 par défaut, donc comportement inchangé) élargit
la zone convoyeur utilisée pour le calcul officiel de
`presence_convoyeur` (pas `nb_zone_machine`, inchangé), pour tester
concrètement une marge de tolérance si le diagnostic la justifie.

    yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --depuis-dossier <dossier> \
        --diag-convoyeur --verite-terrain <fichier verite-terrain>
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

# Zone convoyeur : identique à signature_eval/signature.py (ZONE_CONVOYEUR)
# et à src/fragment_detection.py, dupliquée ici en dur pour la même raison
# (aucune dépendance croisée entre dossiers d'évaluation isolés). C'est la
# zone où le système par pixels (signature_eval/signature.py) et
# l'inspection visuelle ratent parfois le conducteur (immobile, ou masqué
# par le poteau/la pile de cartons) : YOLO est testé spécifiquement dessus.
ZONE_CONVOYEUR = (700, 150, 790, 250)

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


def expand_zone(zone, margin_px):
    x1, y1, x2, y2 = zone
    return (x1 - margin_px, y1 - margin_px, x2 + margin_px, y2 + margin_px)


def overlap_metrics(box, zone):
    """Chevauchement entre une boîte détectée et une zone, indépendamment
    du test centre-dans-zone (`in_zone`) : renvoie (iou, fraction_de_la_boite)
    où fraction_de_la_boite = aire d'intersection / aire de la boîte (utile
    même quand la boîte déborde largement de la zone, ce que l'IoU seul
    minimiserait)."""
    x1, y1, x2, y2 = box
    zx1, zy1, zx2, zy2 = zone
    ix1, iy1 = max(x1, zx1), max(y1, zy1)
    ix2, iy2 = min(x2, zx2), min(y2, zy2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    box_area = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    zone_area = max(0.0, zx2 - zx1) * max(0.0, zy2 - zy1)
    union = box_area + zone_area - inter
    iou = inter / union if union > 0 else 0.0
    frac_box = inter / box_area if box_area > 0 else 0.0
    return iou, frac_box


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
    try:
        # Dossiers style debug_frames/AAAA-MM-JJ_HHMMSS/frame_HHMMSS.jpg
        jour = datetime.strptime(base.split("_")[0], "%Y-%m-%d").date()
    except ValueError:
        # Dossiers sans date dans le nom (ex. frames extraites par
        # signature_eval/extract_frames_dvr.py : frame_HHMMSS.jpg, sans
        # date dans le nom du dossier). La date n'est utilisée que pour
        # construire l'horodatage affiché/sauvegardé (moments clés, noms
        # d'images) ; elle n'entre dans aucun calcul de détection - la
        # date du jour du traitement est un défaut inoffensif ici.
        jour = datetime.now().date()
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
    cx1, cy1, cx2, cy2 = ZONE_CONVOYEUR
    cv2.rectangle(out, (cx1, cy1), (cx2, cy2), (255, 0, 255), 1)
    for (x1, y1, x2, y2), conf, machine, convoyeur in boxes:
        color = (255, 0, 255) if convoyeur else ((0, 0, 255) if machine else (0, 220, 255))
        cv2.rectangle(out, (int(x1), int(y1)), (int(x2), int(y2)), color, 2)
        cv2.putText(out, f"{conf:.2f}", (int(x1), max(12, int(y1) - 4)), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
    n_machine = sum(1 for b in boxes if b[2])
    n_convoyeur = sum(1 for b in boxes if b[3])
    label = (f"{timestamp:%H:%M:%S}  personnes: {len(boxes)}  dont zone machine: {n_machine}"
             f"  dont convoyeur: {n_convoyeur}")
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
    ap.add_argument("--marge-convoyeur-px", type=int, default=0,
                    help="agrandit la zone convoyeur (en pixels) pour le calcul de presence_convoyeur "
                         "uniquement (nb_zone_machine inchangé) ; 0 = comportement identique aux runs précédents")
    ap.add_argument("--diag-convoyeur", action="store_true",
                    help="ajoute une passe d'inférence à très bas seuil (--diag-conf) pour investiguer "
                         "pourquoi la zone convoyeur ne déclenche pas (voir docstring du module)")
    ap.add_argument("--diag-conf", type=float, default=0.01,
                    help="seuil de confiance de la passe diagnostique (défaut Ultralytics ~0.25 ; "
                         "0.01 = quasi aucun filtrage, pour voir toutes les détections candidates)")
    ap.add_argument("--verite-terrain",
                    help="fichier texte (un nom de fichier par ligne) des images où la présence au "
                         "convoyeur est confirmée à l'œil - restreint l'analyse de distribution des "
                         "scores à ces images dans le rapport diagnostique")
    args = ap.parse_args()

    zone_convoyeur_officielle = expand_zone(ZONE_CONVOYEUR, args.marge_convoyeur_px)
    verite_terrain = set()
    if args.verite_terrain:
        with open(args.verite_terrain) as f:
            verite_terrain = {l.strip() for l in f if l.strip()}

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
    diag_rows = []
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
            boxes.append((xyxy, float(b.conf[0]), in_zone(xyxy, ZONE_MACHINE),
                          in_zone(xyxy, zone_convoyeur_officielle)))
        n_machine = sum(1 for b in boxes if b[2])
        n_convoyeur = sum(1 for b in boxes if b[3])
        presence_convoyeur = "oui" if n_convoyeur else "non"
        rows.append([f"{timestamp:%H:%M:%S}", len(boxes), n_machine, n_convoyeur, presence_convoyeur,
                     max((b[1] for b in boxes), default=0.0),
                     " ".join(f"{int(x1)},{int(y1)},{int(x2)},{int(y2)}:{c:.2f}{'M' if m else ''}"
                              for (x1, y1, x2, y2), c, m, _ in boxes)])
        print(f"[{timestamp:%H:%M:%S}] personnes={len(boxes)} zone_machine={n_machine} "
              f"zone_convoyeur={n_convoyeur} presence_convoyeur={presence_convoyeur} "
              f"({infer_times[-1] * 1000:.0f} ms)", flush=True)

        # --- Diagnostic (--diag-convoyeur) : passe séparée à très bas seuil,
        # pour capturer les détections que --conf filtre normalement, et
        # mesurer leur chevauchement avec la zone convoyeur (pas seulement
        # le centre, voir overlap_metrics). N'affecte pas rows/boxes ci-dessus.
        if args.diag_convoyeur:
            res_diag = model.predict(frame, imgsz=args.imgsz, conf=args.diag_conf,
                                      classes=[PERSON_CLASS], device=device, verbose=False)[0]
            nom_attendu = f"frame_{timestamp:%H%M%S}.jpg"
            for b in res_diag.boxes:
                xyxy = [float(v) for v in b.xyxy[0].tolist()]
                conf = float(b.conf[0])
                centre_zone = in_zone(xyxy, ZONE_CONVOYEUR)  # zone SANS marge, test original
                iou, frac_box = overlap_metrics(xyxy, ZONE_CONVOYEUR)
                diag_rows.append([
                    f"{timestamp:%H:%M:%S}", f"{conf:.4f}",
                    *[f"{v:.1f}" for v in xyxy],
                    "oui" if centre_zone else "non",
                    f"{frac_box:.3f}", f"{iou:.3f}",
                    "oui" if nom_attendu in verite_terrain else ("non" if verite_terrain else ""),
                ])

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
        w.writerow(["heure", "nb_personnes", "nb_zone_machine", "nb_zone_convoyeur", "presence_convoyeur",
                    "conf_max", "boites(x1,y1,x2,y2:conf, M=zone machine)"])
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
        f"images avec presence_convoyeur=oui    : {sum(1 for r in rows if r[4] == 'oui')}/{n}"
        f" (taux = {sum(1 for r in rows if r[4] == 'oui') / n:.1%})"
        + (f"  [marge convoyeur = +{args.marge_convoyeur_px}px]" if args.marge_convoyeur_px else ""),
        f"moments cles     : " + ", ".join(
            f"{m}={'OUI' if any(r[0] == m and r[2] for r in rows) else 'non'}" for m in sorted(moments)),
    ]
    with open(os.path.join(out_dir, "resume.txt"), "w") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    print(f"Images annotees et CSV dans {out_dir}", flush=True)

    if args.diag_convoyeur:
        _write_diagnostic(out_dir, diag_rows, args, verite_terrain)


def _write_diagnostic(out_dir, diag_rows, args, verite_terrain):
    with open(os.path.join(out_dir, "diagnostic_convoyeur.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["heure", "conf", "x1", "y1", "x2", "y2",
                    "centre_dans_zone", "fraction_boite_dans_zone", "iou", "verite_terrain"])
        w.writerows(diag_rows)

    # Détections qui recoupent la zone SANS que leur centre n'y soit
    # (hypothèse "boîte déborde") : fraction_boite_dans_zone > 0 mais
    # centre_dans_zone = non.
    debordantes = [r for r in diag_rows if r[6] == "non" and float(r[7]) > 0]
    # Détections dont le centre EST dans la zone (donc déjà comptées par le
    # test officiel à --conf, si leur confiance est suffisante) : sert à
    # mesurer la distribution des scores pour l'hypothèse "seuil trop haut".
    centre_dans_zone = [r for r in diag_rows if r[6] == "oui"]
    if verite_terrain:
        centre_dans_zone_verite = [r for r in centre_dans_zone if r[9] == "oui"]
        debordantes_verite = [r for r in diag_rows if r[9] == "oui" and float(r[7]) > 0]
    else:
        centre_dans_zone_verite = []
        debordantes_verite = []

    def stats_conf(liste):
        if not liste:
            return "aucune détection"
        confs = sorted(float(r[1]) for r in liste)
        n = len(confs)
        mediane = confs[n // 2] if n % 2 else (confs[n // 2 - 1] + confs[n // 2]) / 2
        return (f"n={n}  min={confs[0]:.3f}  mediane={mediane:.3f}  max={confs[-1]:.3f}  "
                f"sous_seuil_actuel({args.conf})={sum(1 for c in confs if c < args.conf)}/{n}")

    lignes = [
        f"Passe diagnostique : conf={args.diag_conf} (vs {args.conf} en usage normal), "
        f"{len(diag_rows)} detection(s) candidate(s) 'person' au total (toutes zones confondues).",
        "",
        "--- Hypothese 2 : seuil de confiance trop haut ---",
        f"Detections dont le CENTRE tombe dans la zone convoyeur (test officiel), toutes images :",
        f"  {stats_conf(centre_dans_zone)}",
    ]
    if verite_terrain:
        lignes += [
            f"Meme chose, restreint aux {len(verite_terrain)} image(s) de verite-terrain "
            f"(presence confirmee a l'oeil) :",
            f"  {stats_conf(centre_dans_zone_verite)}",
        ]
    lignes += [
        "",
        "--- Hypothese 3 : boite deborde de la zone sans que le centre y soit ---",
        f"Detections avec fraction_boite_dans_zone > 0 mais centre_dans_zone = non "
        f"(recoupement sans passer le test officiel), toutes images : {len(debordantes)}",
    ]
    if debordantes:
        ious = sorted(float(r[8]) for r in debordantes)
        fracs = sorted(float(r[7]) for r in debordantes)
        lignes.append(f"  IoU : min={ious[0]:.3f} max={ious[-1]:.3f}  |  "
                       f"fraction de la boite dans la zone : min={fracs[0]:.3f} max={fracs[-1]:.3f}")
    if verite_terrain:
        lignes.append(f"Meme chose, restreint aux images de verite-terrain : {len(debordantes_verite)}")
        if debordantes_verite:
            ious_v = sorted(float(r[8]) for r in debordantes_verite)
            fracs_v = sorted(float(r[7]) for r in debordantes_verite)
            lignes.append(f"  IoU : min={ious_v[0]:.3f} max={ious_v[-1]:.3f}  |  "
                           f"fraction de la boite dans la zone : min={fracs_v[0]:.3f} max={fracs_v[-1]:.3f}")

    contenu = "\n".join(lignes) + "\n"
    with open(os.path.join(out_dir, "diagnostic_convoyeur_resume.txt"), "w") as f:
        f.write(contenu)
    print("\n" + contenu, flush=True)
    print(f"Diagnostic dans {out_dir}/diagnostic_convoyeur.csv et diagnostic_convoyeur_resume.txt", flush=True)


if __name__ == "__main__":
    main()
