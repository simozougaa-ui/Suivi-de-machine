"""Mesure, seconde par seconde, quelle part de TOUTE l'image d'une caméra
change (la protection « image perturbée » de la détection, voir
SEUIL_IMAGE_PERTURBEE dans machine_etat/detecter_marche_arret.py). Outil de
mesure LOCAL : n'envoie rien à l'application de suivi.

16/10 : sur la caméra 10, cette protection jetait 27 à 251 secondes sur 300
entre 15:00 et 15:56 (fichiers de 130 à 176 Mo au lieu de 56 Mo) : trop de
minutes non mesurées. Cet outil dit si ces secondes sont un VRAI défaut
d'image (quasi 100 % des pixels, comme la minute de 00:00) ou une simple
variation (lumière, exposition), pour régler le seuil sur des chiffres.

Même calcul que la détection : image réduite à 160x90 en gris, amplitude
max-min sur les 15 images d'une seconde, un pixel « change » au-dessus de
l'amplitude 25. Affiche aussi la variation de luminosité moyenne de l'image
dans la seconde (une exposition automatique qui oscille la fait monter).

Usage :
    python3 outils_zones/image_perturbee.py DATE HEURE [--canal N] [--duree 60]

Exemple :
    python3 outils_zones/image_perturbee.py 2026-10-16 15:00:00 --canal 10
"""

import argparse
import os
import statistics
import sys
from datetime import datetime, timedelta

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import _commun  # noqa: E402
from machine_etat import lecture_dvr  # noqa: E402
from machine_etat.detecter_marche_arret import (  # noqa: E402
    SEUIL_IMAGE_PERTURBEE, TAILLE_IMAGE_CONTROLE)

SEUIL_AMPLITUDE = 25
FPS = 15
PALIERS = (0.5, 0.7, 0.9)


def stats_seconde(images_grises_reduites, seuil=SEUIL_AMPLITUDE):
    """(part des pixels qui changent 0-1, variation de la luminosité moyenne
    en niveaux de gris) pour UNE seconde d'images grises réduites."""
    pile = np.stack(images_grises_reduites, axis=0).astype(np.int16)
    part = float(((pile.max(axis=0) - pile.min(axis=0)) > seuil).mean())
    moyennes = pile.mean(axis=(1, 2))
    return part, float(moyennes.max() - moyennes.min())


def lire_secondes(capture, max_images, pas=FPS):
    """Lit la capture et renvoie [(part, variation de luminosité), ...] pour
    chaque seconde complète."""
    resultats, tampon = [], []
    for _ in range(max_images):
        ret, frame = capture.read()
        if not ret or frame is None:
            break
        petite = cv2.resize(frame, TAILLE_IMAGE_CONTROLE, interpolation=cv2.INTER_AREA)
        tampon.append(cv2.cvtColor(petite, cv2.COLOR_BGR2GRAY))
        if len(tampon) == pas:
            resultats.append(stats_seconde(tampon))
            tampon = []
    return resultats


def luminosites(capture, n):
    """Luminosité moyenne (0-255) des `n` premières images de la capture,
    image par image : montre si l'image abîmée alterne bonne / mauvaise
    (on pourrait alors écarter les mauvaises) ou reste abîmée en continu."""
    valeurs = []
    for _ in range(n):
        ret, frame = capture.read()
        if not ret or frame is None:
            break
        petite = cv2.resize(frame, TAILLE_IMAGE_CONTROLE, interpolation=cv2.INTER_AREA)
        valeurs.append(float(cv2.cvtColor(petite, cv2.COLOR_BGR2GRAY).mean()))
    return valeurs


def resume(secondes):
    """Ligne de synthèse lisible sur un téléphone."""
    if not secondes:
        return "aucune seconde lue"
    parts = [100 * p for p, _ in secondes]
    lumi = [v for _, v in secondes]
    n = len(secondes)
    paliers = " ".join(f">{int(100 * s)} % : {sum(1 for p, _ in secondes if p > s)}"
                       for s in PALIERS)
    return (f"{n} s | pixels qui changent : médiane {statistics.median(parts):.0f} %, "
            f"max {max(parts):.0f} % | secondes {paliers} | "
            f"luminosité : variation médiane {statistics.median(lumi):.1f}, max {max(lumi):.0f}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date")
    parser.add_argument("heure")
    parser.add_argument("--canal", type=int, help="caméra du DVR (défaut : CAMERA_CHANNEL du .env)")
    parser.add_argument("--duree", type=int, default=60, help="secondes lues (défaut 60)")
    parser.add_argument("--trace", type=int, metavar="N", default=0,
                        help="affiche la luminosité moyenne des N premières images, une par une")
    args = parser.parse_args()
    try:
        debut = datetime.strptime(f"{args.date} {args.heure}", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    fin = debut + timedelta(seconds=min(args.duree, 10) if args.trace else args.duree)
    try:
        with lecture_dvr.fenetre_telechargee(debut, fin, canal=args.canal) as (chemin, octets):
            capture, chemin_lu = lecture_dvr.ouvrir_fichier(chemin)
            try:
                if args.trace:
                    valeurs = luminosites(capture, args.trace)
                    print(f"{debut:%d/%m %H:%M:%S} luminosité image par image (0-255) : "
                          + " ".join(f"{v:.0f}" for v in valeurs))
                    return
                secondes = lire_secondes(capture, args.duree * FPS + FPS)
            finally:
                capture.release()
                if chemin_lu != chemin:
                    lecture_dvr._supprimer(chemin_lu)
    except (lecture_dvr.ErreurTelechargement, lecture_dvr.ErreurLectureFichier,
            lecture_dvr.ErreurPasDEnregistrement) as e:
        sys.exit(f"Lecture impossible : {lecture_dvr.masquer(e)}")
    print(f"{debut:%d/%m %H:%M} ({octets / (1024 * 1024):.0f} Mo) : {resume(secondes)}")
    print(f"(la détection ignore une seconde au-dessus de {100 * SEUIL_IMAGE_PERTURBEE:.0f} %)")


if __name__ == "__main__":
    main()
