"""Évaluation ISOLÉE d'une identification du conducteur par signature de
couleur vestimentaire (haut / bas du corps), pour compenser les cas où
YOLO ne voit aucune silhouette humaine (conducteur penché au convoyeur,
voir yolo_eval/).

Ne touche à aucun fichier de production, ni à yolo_eval/. Travaille sur
des images fixes déjà extraites (aucun accès DVR/Jetson nécessaire pour
ce test de faisabilité) : un dossier d'images, une image de référence
"machine vide" (même résolution, pour isoler par soustraction les pixels
qui changent = corps/vêtements), une image+boîte où le conducteur est
identifié ailleurs sur la machine (silhouette entière ou bien visible),
et la liste des images où il est seulement visible, partiellement, dans
la zone convoyeur.

Méthode (choisie et justifiée dans NOTES-SESSION.md) :
1. Masque de premier plan = pixels dont la différence avec la référence
   dépasse un seuil (mêmes principes que src/fragment_detection.py, mais
   ici pour isoler la couleur du vêtement, pas mesurer une intensité de
   mouvement).
2. Signature = couleur MÉDIANE (robuste aux reflets/ombres) des pixels du
   masque, en espace Lab (perceptuellement plus proche de la vision
   humaine que RGB/HSV brut).
3. Comparaison = distance euclidienne sur la seule CHROMINANCE (a, b),
   sans la luminance (L). Choix fait après mesure (voir NOTES-SESSION.md,
   "Évaluation signature de couleur") : la luminance L varie énormément
   d'une image à l'autre (éclairage, reflets, contamination du masque par
   la pile de feuilles qui bouge aussi) alors que la chrominance reste
   stable pour un même vêtement. Sur les 15 images convoyeur, la distance
   Lab complète (L inclus) donne un bruit intra-personne moyen de ~34 ;
   la distance sur (a, b) seul donne ~1.7 pour les mêmes 15 images
   (le même conducteur, au même endroit) contre ~16 pour un autre ouvrier
   (chemise bleue) — un rapport signal/bruit ~10x meilleur.

Usage :
    python3 signature_eval/signature.py \
        --frames-dir <dossier images> --reference <image machine vide> \
        --signature-image <image> --signature-box x1,y1,x2,y2 \
        --occluded-list <fichier .txt, un nom de fichier par ligne> \
        --out-dir signature_eval/out
"""

import argparse
import csv
import json
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))

# Zone convoyeur : identique à src/fragment_detection.py (lue en dur ici,
# aucun import du code de production).
ZONE_CONVOYEUR = (700, 150, 790, 250)

# Seuil de différence par canal (0-255) pour qu'un pixel soit considéré
# "changé" par rapport à la référence machine vide -> pixel de
# corps/vêtement. Choisi empiriquement (voir NOTES-SESSION.md) : assez
# haut pour ignorer le bruit vidéo/JPEG (~5-8), assez bas pour capter des
# vêtements sombres sur fond sombre.
DIFF_THRESHOLD = 18

# Seuil de similarité de couleur : distance euclidienne sur la seule
# chrominance (a, b) de Lab (voir justification ci-dessus). Calibré sur
# les mesures de cette évaluation : bruit intra-personne ~1.7-2.8,
# distance vers un autre ouvrier (chemise bleue) ~14-19. 8 est un
# compromis (à mi-chemin), volontairement prudent car basé sur une seule
# image de signature de référence, pas un jeu de données validé.
MATCH_THRESHOLD_AB = 8.0


def foreground_mask(frame, reference, box):
    x1, y1, x2, y2 = box
    f = frame[y1:y2, x1:x2].astype(np.int16)
    r = reference[y1:y2, x1:x2].astype(np.int16)
    diff = np.abs(f - r).max(axis=2)  # max sur B,G,R : un seul canal qui bouge suffit
    return diff > DIFF_THRESHOLD


def median_color_lab(frame, box, mask):
    x1, y1, x2, y2 = box
    crop = frame[y1:y2, x1:x2]
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float64)
    pixels = lab[mask]
    if pixels.size == 0:
        return None
    return np.median(pixels, axis=0)  # [L, a, b]


def lab_distance(a, b):
    """Distance Lab complète (L, a, b) : donnée à titre informatif
    seulement, très sensible à l'exposition/aux reflets (voir docstring)."""
    return float(np.linalg.norm(np.array(a) - np.array(b)))


def ab_distance(a, b):
    """Distance sur la chrominance seule (a, b) : métrique retenue pour
    la décision de correspondance (voir docstring du module)."""
    a, b = np.array(a), np.array(b)
    return float(np.hypot(a[1] - b[1], a[2] - b[2]))


def annotate(frame, box, mask, label_lines, mask_color=(0, 255, 255)):
    out = frame.copy()
    x1, y1, x2, y2 = box
    overlay = out[y1:y2, x1:x2]
    colored = np.zeros_like(overlay)
    colored[mask] = mask_color
    overlay[:] = cv2.addWeighted(overlay, 0.6, colored, 0.4, 0)
    cv2.rectangle(out, (x1, y1), (x2, y2), (255, 255, 0), 1)
    y = 20
    cv2.rectangle(out, (0, 0), (out.shape[1], 18 + 18 * len(label_lines)), (0, 0, 0), -1)
    for line in label_lines:
        cv2.putText(out, line, (6, y), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        y += 18
    return out


def lab_to_bgr_swatch(lab_color, size=40):
    swatch = np.full((size, size, 3), lab_color, dtype=np.float32)
    return cv2.cvtColor(swatch.astype(np.uint8), cv2.COLOR_LAB2BGR)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--reference", required=True, help="image 'machine vide' (même résolution)")
    ap.add_argument("--signature-image", required=True, help="nom de fichier (dans frames-dir) où le conducteur est visible ailleurs")
    ap.add_argument("--signature-box", required=True, help="x1,y1,x2,y2 de la personne dans signature-image")
    ap.add_argument("--occluded-list", required=True, help="fichier texte : un nom de fichier par ligne (images convoyeur)")
    ap.add_argument("--control-image", help="nom de fichier d'une autre personne (ex. ouvrier du fond), pour tester la confusion")
    ap.add_argument("--control-box", help="x1,y1,x2,y2 de cette autre personne")
    ap.add_argument("--out-dir", default=os.path.join(HERE, "out"))
    args = ap.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    reference = cv2.imread(args.reference)
    if reference is None:
        raise SystemExit(f"référence introuvable : {args.reference}")

    def load(name):
        p = os.path.join(args.frames_dir, name)
        im = cv2.imread(p)
        if im is None:
            raise SystemExit(f"image introuvable : {p}")
        return im

    def parse_box(s):
        return tuple(int(v) for v in s.split(","))

    # --- 1. Signature du conducteur, mesurée ailleurs sur la machine ---
    sig_box = parse_box(args.signature_box)
    sig_frame = load(args.signature_image)
    sig_mask = foreground_mask(sig_frame, reference, sig_box)
    sig_color = median_color_lab(sig_frame, sig_box, sig_mask)
    sig_pixels = int(sig_mask.sum())
    print(f"[signature] {args.signature_image} boite={sig_box} "
          f"pixels_masque={sig_pixels} couleur_Lab={sig_color}", flush=True)
    cv2.imwrite(
        os.path.join(args.out_dir, "signature_source.jpg"),
        annotate(sig_frame, sig_box, sig_mask,
                  [f"signature conducteur : {os.path.basename(args.signature_image)}",
                   f"Lab={np.round(sig_color, 1).tolist()}  pixels={sig_pixels}"]),
        [cv2.IMWRITE_JPEG_QUALITY, 90])

    # --- 2. Témoin (autre ouvrier), pour vérifier qu'on ne confond pas ---
    ctrl_color = None
    if args.control_image and args.control_box:
        ctrl_box = parse_box(args.control_box)
        ctrl_frame = load(args.control_image)
        ctrl_mask = foreground_mask(ctrl_frame, reference, ctrl_box)
        ctrl_color = median_color_lab(ctrl_frame, ctrl_box, ctrl_mask)
        d_ctrl_vs_sig = lab_distance(ctrl_color, sig_color) if ctrl_color is not None else None
        print(f"[temoin] {args.control_image} boite={ctrl_box} "
              f"couleur_Lab={ctrl_color} distance_vs_conducteur={d_ctrl_vs_sig:.1f}", flush=True)
        cv2.imwrite(
            os.path.join(args.out_dir, "temoin_autre_ouvrier.jpg"),
            annotate(ctrl_frame, ctrl_box, ctrl_mask,
                      [f"temoin (autre ouvrier) : {os.path.basename(args.control_image)}",
                       f"Lab={np.round(ctrl_color, 1).tolist()}  distance/conducteur={d_ctrl_vs_sig:.1f}"],
                      mask_color=(255, 0, 255)),
            [cv2.IMWRITE_JPEG_QUALITY, 90])

    # --- 3. Zone convoyeur, image par image ---
    with open(args.occluded_list) as f:
        names = [l.strip() for l in f if l.strip()]

    rows = []
    colors = []
    zone_area = (ZONE_CONVOYEUR[2] - ZONE_CONVOYEUR[0]) * (ZONE_CONVOYEUR[3] - ZONE_CONVOYEUR[1])
    n_saved = 0
    for name in names:
        frame = load(name)
        mask = foreground_mask(frame, reference, ZONE_CONVOYEUR)
        n_px = int(mask.sum())
        frac = n_px / zone_area
        color = median_color_lab(frame, ZONE_CONVOYEUR, mask)
        if color is None:
            rows.append([name, n_px, f"{frac:.2f}", "", "", "aucun pixel"])
            continue
        d_sig_lab = lab_distance(color, sig_color)
        d_sig_ab = ab_distance(color, sig_color)
        match = "OUI" if d_sig_ab < MATCH_THRESHOLD_AB else "non"
        colors.append(color)
        rows.append([name, n_px, f"{frac:.3f}", np.round(color, 1).tolist(),
                     f"{d_sig_lab:.1f}", f"{d_sig_ab:.1f}", match])
        print(f"[convoyeur] {name}: pixels={n_px} ({frac:.0%} de la zone) "
              f"Lab={np.round(color, 1)} distance_Lab={d_sig_lab:.1f} "
              f"distance_ab={d_sig_ab:.1f} -> {match}", flush=True)
        if n_saved < 6:
            n_saved += 1
            cv2.imwrite(
                os.path.join(args.out_dir, f"convoyeur_{n_saved:02d}_{os.path.splitext(name)[0]}.jpg"),
                annotate(frame, ZONE_CONVOYEUR, mask,
                          [f"{name}  pixels_visibles={n_px} ({frac:.0%} de la zone)",
                           f"Lab mesure={np.round(color, 1).tolist()}  "
                           f"distance_ab/signature={d_sig_ab:.1f}  match={match}"]),
                [cv2.IMWRITE_JPEG_QUALITY, 90])

    with open(os.path.join(args.out_dir, "mesures.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["image", "pixels_masque", "fraction_zone", "Lab",
                    "distance_Lab_signature", "distance_ab_signature", "correspondance"])
        w.writerows(rows)

    # --- Résumé : cohérence interne des 15 mesures (sans dépendre de la
    # signature externe) : si le vêtement est bien le même sur les 15
    # images, leurs couleurs mesurées doivent être proches ENTRE ELLES.
    # Calculé en Lab complet ET en chrominance seule (a, b), pour montrer
    # l'effet du choix de métrique (voir NOTES-SESSION.md).
    summary = {
        "signature_conducteur_Lab": sig_color.tolist() if sig_color is not None else None,
        "signature_pixels": sig_pixels,
        "temoin_autre_ouvrier_Lab": ctrl_color.tolist() if ctrl_color is not None else None,
        "n_images_convoyeur": len(names),
        "n_avec_pixels_visibles": len(colors),
    }
    if colors:
        arr = np.array(colors)
        mean_c = arr.mean(axis=0)
        dists_intra_lab = [lab_distance(c, mean_c) for c in arr]
        dists_intra_ab = [ab_distance(c, mean_c) for c in arr]
        summary["couleur_moyenne_convoyeur_Lab"] = mean_c.tolist()
        summary["distance_Lab_intra_convoyeur_moyenne"] = float(np.mean(dists_intra_lab))
        summary["distance_Lab_intra_convoyeur_max"] = float(np.max(dists_intra_lab))
        summary["distance_ab_intra_convoyeur_moyenne"] = float(np.mean(dists_intra_ab))
        summary["distance_ab_intra_convoyeur_max"] = float(np.max(dists_intra_ab))
        summary["distance_ab_moyenne_convoyeur_vs_signature"] = float(
            np.mean([ab_distance(c, sig_color) for c in arr])) if sig_color is not None else None
        if ctrl_color is not None:
            summary["distance_ab_moyenne_convoyeur_vs_temoin"] = float(
                np.mean([ab_distance(c, ctrl_color) for c in arr]))

    with open(os.path.join(args.out_dir, "resume.json"), "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(json.dumps(summary, indent=2, ensure_ascii=False), flush=True)
    print(f"Resultats dans {args.out_dir}", flush=True)


if __name__ == "__main__":
    main()
