"""Grille de repérage de coordonnées sur le coin haut-gauche de l'image
(x 0-520, y 0-260 du flux 1280x720), pour ajuster outils_zones/zones.json
sans deviner les pixels à l'oeil. Outil de mesure LOCAL : n'envoie rien à
l'application de suivi.

Usage :
    python3 outils_zones/quadrillage.py DATE HEURE [--canal N] [--entier]

  --canal N  lit la caméra N du DVR au lieu de CAMERA_CHANNEL du .env
             (sans rien modifier dans .env) ;
  --entier   toute l'image (1280x720, sans agrandissement, grille tous les
             40 px, coordonnées tous les 80 px) au lieu du coin haut-gauche :
             pour repérer une machine sur une NOUVELLE caméra (16/10 :
             ajout de la caméra 10).

Exemples :
    python3 outils_zones/quadrillage.py 2026-09-30 10:38:00
    python3 outils_zones/quadrillage.py 2026-10-15 11:00:00 --canal 10 --entier
"""

import argparse
import os
import sys
from datetime import datetime

import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _commun  # noqa: E402

ZONE_COIN = (0, 0, 520, 260)  # x1, y1, x2, y2 dans l'image source
ZOOM = 3
PAS_LIGNES = 20
PAS_ETIQUETTES = 40
ROUGE = (0, 0, 255)
FICHIER_SORTIE = os.path.join(_commun.SORTIES_DIR, "quadrillage.jpg")

# Image entière (--entier) : pas d'agrandissement, grille plus large.
PAS_LIGNES_ENTIER = 40
PAS_ETIQUETTES_ENTIER = 80


def construire_quadrillage(frame):
    x1, y1, x2, y2 = ZONE_COIN
    crop = frame[y1:y2, x1:x2]
    agrandi = cv2.resize(crop, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)
    largeur, hauteur = x2 - x1, y2 - y1

    for x in range(0, largeur + 1, PAS_LIGNES):
        cv2.line(agrandi, (x * ZOOM, 0), (x * ZOOM, agrandi.shape[0]), ROUGE, 1)
    for y in range(0, hauteur + 1, PAS_LIGNES):
        cv2.line(agrandi, (0, y * ZOOM), (agrandi.shape[1], y * ZOOM), ROUGE, 1)

    for x in range(0, largeur + 1, PAS_ETIQUETTES):
        for y in range(0, hauteur + 1, PAS_ETIQUETTES):
            cv2.putText(
                agrandi, f"{x},{y}", (x * ZOOM + 3, y * ZOOM + 18),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, ROUGE, 2, cv2.LINE_AA,
            )

    return agrandi


def construire_quadrillage_entier(frame, titre=""):
    """Toute l'image, grille tous les PAS_LIGNES_ENTIER px et coordonnées
    « x,y » tous les PAS_ETIQUETTES_ENTIER px, plus un titre (caméra, heure)
    en bas à gauche. Mêmes coordonnées que l'image source : les valeurs lues
    vont telles quelles dans une zone (x1, y1, x2, y2)."""
    image = frame.copy()
    hauteur, largeur = image.shape[:2]
    for x in range(0, largeur + 1, PAS_LIGNES_ENTIER):
        cv2.line(image, (x, 0), (x, hauteur), ROUGE, 1)
    for y in range(0, hauteur + 1, PAS_LIGNES_ENTIER):
        cv2.line(image, (0, y), (largeur, y), ROUGE, 1)
    for x in range(0, largeur, PAS_ETIQUETTES_ENTIER):
        for y in range(0, hauteur, PAS_ETIQUETTES_ENTIER):
            cv2.putText(image, f"{x},{y}", (x + 2, y + 14),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.4, ROUGE, 1, cv2.LINE_AA)
    if titre:
        cv2.putText(image, titre, (10, hauteur - 12), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (0, 255, 255), 2, cv2.LINE_AA)
    return image


def nom_sortie(canal=None, entier=False):
    """quadrillage.jpg (comportement historique), sinon un nom qui dit la
    caméra et le format, pour ne pas écraser l'image de la caméra 15."""
    if canal is None and not entier:
        return FICHIER_SORTIE
    nom = "quadrillage" + (f"_canal{canal}" if canal is not None else "") + \
        ("_entier" if entier else "") + ".jpg"
    return os.path.join(_commun.SORTIES_DIR, nom)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date")
    parser.add_argument("heure")
    parser.add_argument("--canal", type=int, help="caméra du DVR (défaut : CAMERA_CHANNEL du .env)")
    parser.add_argument("--entier", action="store_true", help="toute l'image au lieu du coin haut-gauche")
    args = parser.parse_args()
    if args.canal is not None:
        # Lu à chaque construction d'URL (src/camera_stream.py) : remplace
        # la valeur du .env pour CE lancement seulement.
        os.environ["CAMERA_CHANNEL"] = str(args.canal)

    try:
        instant = datetime.strptime(f"{args.date} {args.heure}", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    print(f"Lecture de {instant:%Y-%m-%d %H:%M:%S}...")
    frame = _commun.capturer_image(instant)

    if args.entier:
        canal = args.canal if args.canal is not None else os.getenv("CAMERA_CHANNEL", "?")
        quadrillage = construire_quadrillage_entier(
            frame, f"Camera {canal} - {instant:%Y-%m-%d %H:%M:%S}")
    else:
        quadrillage = construire_quadrillage(frame)
    sortie = nom_sortie(args.canal, args.entier)
    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    cv2.imwrite(sortie, quadrillage)
    print(f"OK : {sortie}")


if __name__ == "__main__":
    main()
