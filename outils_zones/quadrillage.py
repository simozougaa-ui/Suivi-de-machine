"""Grille de repérage de coordonnées sur le coin haut-gauche de l'image
(x 0-520, y 0-260 du flux 1280x720), pour ajuster outils_zones/zones.json
sans deviner les pixels à l'oeil. Outil de mesure LOCAL : n'envoie rien à
l'application de suivi.

Usage :
    python3 outils_zones/quadrillage.py DATE HEURE

Exemple :
    python3 outils_zones/quadrillage.py 2026-09-30 10:38:00
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


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date")
    parser.add_argument("heure")
    args = parser.parse_args()

    try:
        instant = datetime.strptime(f"{args.date} {args.heure}", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    print(f"Lecture de {instant:%Y-%m-%d %H:%M:%S}...")
    frame = _commun.capturer_image(instant)

    quadrillage = construire_quadrillage(frame)
    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    cv2.imwrite(FICHIER_SORTIE, quadrillage)
    print(f"OK : {FICHIER_SORTIE}")


if __name__ == "__main__":
    main()
