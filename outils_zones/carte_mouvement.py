"""Carte de mouvement d'une région d'une caméra : pour choisir la zone de
détection d'une NOUVELLE machine sans deviner les pixels (16/10 : Encolleuse 2
sur la caméra 10). Outil de mesure LOCAL : n'envoie rien à l'application de
suivi, ne touche à aucun fichier de production.

Lit --duree secondes à pleine cadence. Méthode IDENTIQUE à zones.py (gris,
flou gaussien (5,5), amplitude max-min par pixel sur les 15 images d'une
seconde, seuil de pixel 25). Pour chaque pixel de la région, compte les
secondes où il a bougé, puis dessine la région agrandie avec :
  - en ROUGE les pixels en mouvement au moins 40 % des secondes (ce qui
    bouge en continu : la machine qui tourne) ;
  - en ORANGE ceux en mouvement 10 à 40 % du temps (passages, gestes) ;
  - une grille tous les 10 px et les coordonnées de l'image source tous les
    40 px, utilisables telles quelles pour une zone (x1, y1, x2, y2).

Usage :
    python3 outils_zones/carte_mouvement.py DATE HEURE --region X1,Y1,X2,Y2 [--canal N] [--duree 20]

Exemple (Encolleuse 2, caméra 10) :
    python3 outils_zones/carte_mouvement.py 2026-10-15 20:00:00 --canal 10 --region 160,120,680,360
"""

import argparse
import os
import sys
from datetime import datetime, timedelta

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _commun  # noqa: E402
from src.camera_stream import build_rtsp_playback_url, open_stream  # noqa: E402
from zones import FLOU, FPS, SEUIL_PIXEL  # noqa: E402

ZOOM = 2
PAS_LIGNES = 10
PAS_ETIQUETTES = 40
FRACTION_CONTINU = 0.40     # même règle des 40 % que la détection
FRACTION_PASSAGE = 0.10
ROUGE = (0, 0, 255)
ORANGE = (0, 165, 255)
GRILLE = (90, 90, 90)
BLANC = (255, 255, 255)


def lire_region(texte):
    """« x1,y1,x2,y2 » -> (x1, y1, x2, y2), avec x1 < x2 et y1 < y2."""
    try:
        x1, y1, x2, y2 = (int(v) for v in texte.split(","))
    except ValueError:
        raise argparse.ArgumentTypeError("région attendue : X1,Y1,X2,Y2 (entiers)")
    if x1 < 0 or y1 < 0 or x2 <= x1 or y2 <= y1:
        raise argparse.ArgumentTypeError("région invalide : il faut 0 <= x1 < x2 et 0 <= y1 < y2")
    return x1, y1, x2, y2


def masque_seconde(images_grises_floutees, region, seuil_pixel=SEUIL_PIXEL):
    """Booléen 2D (taille de la région) : pixels dont l'amplitude max-min sur
    les images d'une seconde dépasse `seuil_pixel` (même calcul que
    zones.fraction_mouvement, sans la moyenne)."""
    x1, y1, x2, y2 = region
    pile = np.stack(images_grises_floutees, axis=0)[:, y1:y2, x1:x2].astype(np.int16)
    return (pile.max(axis=0) - pile.min(axis=0)) > seuil_pixel


def fraction_par_pixel(masques):
    """Part des secondes (0 à 1) où chaque pixel a bougé."""
    if not masques:
        raise ValueError("aucune seconde complète lue")
    return np.mean(np.stack(masques, axis=0), axis=0)


def dessiner_carte(image, region, fraction, zoom=ZOOM, titre=""):
    """Région de `image` agrandie, pixels en mouvement colorés, grille et
    coordonnées de l'image SOURCE."""
    x1, y1, x2, y2 = region
    vue = image[y1:y2, x1:x2].copy()
    calque = vue.copy()
    calque[fraction >= FRACTION_PASSAGE] = ORANGE
    calque[fraction >= FRACTION_CONTINU] = ROUGE
    vue = cv2.addWeighted(calque, 0.6, vue, 0.4, 0)
    vue = cv2.resize(vue, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    hauteur, largeur = vue.shape[:2]
    # Lignes sur les coordonnées RONDES de la source (multiples du pas), pas
    # sur le bord de la région : les étiquettes se lisent directement.
    for x in range(x1 + (-x1) % PAS_LIGNES, x2 + 1, PAS_LIGNES):
        cv2.line(vue, ((x - x1) * zoom, 0), ((x - x1) * zoom, hauteur), GRILLE, 1)
    for y in range(y1 + (-y1) % PAS_LIGNES, y2 + 1, PAS_LIGNES):
        cv2.line(vue, (0, (y - y1) * zoom), (largeur, (y - y1) * zoom), GRILLE, 1)
    for x in range(x1 + (-x1) % PAS_ETIQUETTES, x2, PAS_ETIQUETTES):
        for y in range(y1 + (-y1) % PAS_ETIQUETTES, y2, PAS_ETIQUETTES):
            cv2.putText(vue, f"{x},{y}", ((x - x1) * zoom + 2, (y - y1) * zoom + 12),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, BLANC, 1, cv2.LINE_AA)
    if titre:
        cv2.putText(vue, titre, (6, hauteur - 8), cv2.FONT_HERSHEY_SIMPLEX,
                    0.6, (0, 255, 255), 2, cv2.LINE_AA)
    return vue


def nom_sortie(canal):
    nom = "carte_mouvement" + (f"_canal{canal}" if canal is not None else "") + ".jpg"
    return os.path.join(_commun.SORTIES_DIR, nom)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date")
    parser.add_argument("heure")
    parser.add_argument("--region", type=lire_region, required=True, help="X1,Y1,X2,Y2 dans l'image source")
    parser.add_argument("--canal", type=int, help="caméra du DVR (défaut : CAMERA_CHANNEL du .env)")
    parser.add_argument("--duree", type=int, default=20, help="secondes lues (défaut 20)")
    args = parser.parse_args()
    if args.canal is not None:
        os.environ["CAMERA_CHANNEL"] = str(args.canal)   # ce lancement seulement
    try:
        debut = datetime.strptime(f"{args.date} {args.heure}", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    print(f"Lecture de {args.duree} s à partir de {debut:%Y-%m-%d %H:%M:%S}...")
    capture = open_stream(build_rtsp_playback_url(debut, debut + timedelta(seconds=args.duree)))
    premiere, tampon, masques = None, [], []
    try:
        for _ in range(args.duree * FPS + FPS):
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            if premiere is None:
                premiere = frame
                h, w = frame.shape[:2]
                if args.region[2] > w or args.region[3] > h:
                    sys.exit(f"Région hors de l'image ({w}x{h}).")
            tampon.append(cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), FLOU, 0))
            if len(tampon) == FPS:
                masques.append(masque_seconde(tampon, args.region))
                tampon = []
    finally:
        capture.release()
    if premiere is None or not masques:
        sys.exit("Aucune seconde complète lue (DVR indisponible ou pas d'enregistrement à cette heure).")

    canal = args.canal if args.canal is not None else os.getenv("CAMERA_CHANNEL", "?")
    carte = dessiner_carte(premiere, args.region, fraction_par_pixel(masques),
                           titre=f"Camera {canal} - {debut:%Y-%m-%d %H:%M:%S} - {len(masques)} s")
    sortie = nom_sortie(args.canal)
    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    cv2.imwrite(sortie, carte)
    print(f"OK : {sortie} ({len(masques)} s analysées)")


if __name__ == "__main__":
    main()
