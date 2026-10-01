"""Compare visuellement les zones de mesure (outils_zones/zones.json) à
deux instants différents (ex: machine en marche vs à l'arrêt, jour vs
nuit) : chaque zone est agrandie 5x avec son rectangle de mesure dessiné,
les deux instants côte à côte. Outil de mesure LOCAL : n'envoie rien à
l'application de suivi.

Usage :
    python3 outils_zones/zoom.py DATE1 HEURE1 DATE2 HEURE2

Exemple (jour vs nuit) :
    python3 outils_zones/zoom.py 2026-09-30 10:38:00 2026-10-01 03:56:00
"""

import argparse
import os
import sys
from datetime import datetime

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _commun  # noqa: E402

ZOOM = 5
MARGE = 10
ROUGE = (0, 0, 255)
BANDEAU_HAUTEUR = 36
FICHIER_SORTIE = os.path.join(_commun.SORTIES_DIR, "zones_comparaison.jpg")


def vignette_zone(frame, zone):
    """Recadre `frame` autour de `zone` (+MARGE px), agrandit ZOOM fois et
    dessine le rectangle de mesure exact en rouge."""
    x1, y1, x2, y2 = zone
    hauteur_frame, largeur_frame = frame.shape[:2]
    cx1, cy1 = max(0, x1 - MARGE), max(0, y1 - MARGE)
    cx2, cy2 = min(largeur_frame, x2 + MARGE), min(hauteur_frame, y2 + MARGE)
    crop = frame[cy1:cy2, cx1:cx2]
    agrandi = cv2.resize(crop, None, fx=ZOOM, fy=ZOOM, interpolation=cv2.INTER_NEAREST)

    rx1, ry1 = (x1 - cx1) * ZOOM, (y1 - cy1) * ZOOM
    rx2, ry2 = (x2 - cx1) * ZOOM, (y2 - cy1) * ZOOM
    cv2.rectangle(agrandi, (rx1, ry1), (rx2, ry2), ROUGE, 2)
    return agrandi


def etiqueter(vignette, texte):
    """Ajoute un bandeau noir en haut de `vignette` avec `texte` en gros
    caractères blancs (lisible sur un écran de téléphone)."""
    bandeau = np.zeros((BANDEAU_HAUTEUR, vignette.shape[1], 3), dtype=np.uint8)
    cv2.putText(bandeau, texte, (4, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
    return np.vstack([bandeau, vignette])


def construire_ligne(frame, instant, zones):
    """Une rangée horizontale : une cellule étiquetée par zone, alignées
    sur la même hauteur (les zones n'ont pas toutes la même taille)."""
    cellules = [etiqueter(vignette_zone(frame, zone), f"{nom}  {instant:%H:%M:%S}") for nom, zone in zones.items()]
    hauteur_max = max(c.shape[0] for c in cellules)
    cellules = [
        cv2.copyMakeBorder(c, 0, hauteur_max - c.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        for c in cellules
    ]
    return np.hstack(cellules)


def construire_page(frame1, instant1, frame2, instant2, zones):
    ligne1 = construire_ligne(frame1, instant1, zones)
    ligne2 = construire_ligne(frame2, instant2, zones)
    largeur_max = max(ligne1.shape[1], ligne2.shape[1])
    ligne1, ligne2 = (
        cv2.copyMakeBorder(l, 0, 0, 0, largeur_max - l.shape[1], cv2.BORDER_CONSTANT, value=(0, 0, 0))
        for l in (ligne1, ligne2)
    )
    return np.vstack([ligne1, ligne2])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date1")
    parser.add_argument("heure1")
    parser.add_argument("date2")
    parser.add_argument("heure2")
    args = parser.parse_args()

    try:
        instant1 = datetime.strptime(f"{args.date1} {args.heure1}", "%Y-%m-%d %H:%M:%S")
        instant2 = datetime.strptime(f"{args.date2} {args.heure2}", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    zones = _commun.charger_zones()

    print(f"Lecture de {instant1:%Y-%m-%d %H:%M:%S}...")
    frame1 = _commun.capturer_image(instant1)
    print(f"Lecture de {instant2:%Y-%m-%d %H:%M:%S}...")
    frame2 = _commun.capturer_image(instant2)

    page = construire_page(frame1, instant1, frame2, instant2, zones)
    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    cv2.imwrite(FICHIER_SORTIE, page)
    print(f"OK : {FICHIER_SORTIE}")


if __name__ == "__main__":
    main()
