"""Mesure le mouvement dans les zones nommées (outils_zones/zones.json) à
un ou plusieurs instants d'un enregistrement du DVR. Outil de mesure
LOCAL : n'envoie rien à l'application de suivi, indépendant de
machine_etat/detecter_marche_arret.py.

Lit l'enregistrement à pleine cadence (15 images/s, aucune saute) sur la
durée demandée. Pour chaque seconde (15 images), et pour chaque zone,
calcule le % de pixels dont l'amplitude (max - min sur les 15 images)
dépasse 25, après passage en gris et flou gaussien (5,5) — même ordre
gris -> flou que le correctif du 2026-10-01 de detecter_marche_arret.py,
pour que les zones testées ici restent comparables à la production.

Usage :
    python3 outils_zones/zones.py DATE HEURE [DATE HEURE ...] [--duree 60]

Exemple (jour vs nuit) :
    python3 outils_zones/zones.py 2026-09-30 10:38:00 2026-10-01 03:56:00 --duree 60
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
from src.camera_stream import build_rtsp_playback_url, open_stream  # noqa: E402

FPS = 15
SEUIL_PIXEL = 25
SEUIL_SECONDE_POURCENT = 5.0
FLOU = (5, 5)


def fraction_mouvement(images_grises_floutees, zone, seuil_pixel=SEUIL_PIXEL):
    """`images_grises_floutees` : liste d'images 2D en niveaux de gris déjà
    flloutées (même forme). Retourne le % de pixels de `zone` dont
    l'amplitude max-min sur ces images dépasse `seuil_pixel`."""
    x1, y1, x2, y2 = zone
    pile = np.stack(images_grises_floutees, axis=0)[:, y1:y2, x1:x2].astype(np.int16)
    amplitude = pile.max(axis=0) - pile.min(axis=0)
    return 100.0 * np.count_nonzero(amplitude > seuil_pixel) / amplitude.size


def analyser_instant(debut, duree_s, zones):
    """Lit `duree_s` secondes à pleine cadence depuis `debut` et retourne
    {nom_zone: [fraction_par_seconde, ...]}. S'arrête proprement (sans
    lever d'exception) si le flux se termine avant la durée demandée
    (fin d'enregistrement, timeout DVR en fin de période) : ce qui a été
    lu est gardé, une dernière seconde incomplète (<15 images) est jetée
    plutôt que faussée."""
    fin = debut + timedelta(seconds=duree_s)
    url = build_rtsp_playback_url(debut, fin)
    capture = open_stream(url)
    resultats = {nom: [] for nom in zones}
    tampon = []
    # Marge d'une seconde si le flux dépasse légèrement `fin` : évite une
    # boucle sans fin si le DVR ne coupe pas exactement à l'heure demandée.
    max_images = duree_s * FPS + FPS
    lues = 0
    try:
        while lues < max_images:
            ret, frame = capture.read()
            if not ret or frame is None:
                break
            lues += 1
            gris = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            flou = cv2.GaussianBlur(gris, FLOU, 0)
            tampon.append(flou)
            if len(tampon) == FPS:
                for nom, zone in zones.items():
                    resultats[nom].append(fraction_mouvement(tampon, zone))
                tampon = []
    finally:
        capture.release()
    return resultats


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("instants", nargs="+", metavar="DATE HEURE",
                         help="Une ou plusieurs paires DATE (AAAA-MM-JJ) HEURE (HH:MM:SS)")
    parser.add_argument("--duree", type=int, default=60, help="Duree a analyser par instant, en secondes (defaut 60)")
    args = parser.parse_args()

    if len(args.instants) % 2 != 0:
        parser.error("les instants doivent etre donnes par paires DATE HEURE.")

    paires = list(zip(args.instants[0::2], args.instants[1::2]))
    try:
        debuts = [datetime.strptime(f"{d} {h}", "%Y-%m-%d %H:%M:%S") for d, h in paires]
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    zones = _commun.charger_zones()
    recap = {}

    for debut in debuts:
        print(f"\n=== {debut:%Y-%m-%d %H:%M:%S} (duree {args.duree}s) ===")
        try:
            resultats = analyser_instant(debut, args.duree, zones)
        except ConnectionError as exc:
            print(f"  Erreur : {exc}")
            continue

        recap[debut] = {}
        for nom in zones:
            valeurs = resultats[nom]
            if not valeurs:
                print(f"  Zone {nom}: aucune seconde complete lue")
                recap[debut][nom] = None
                continue
            moyenne = statistics.mean(valeurs)
            mediane = statistics.median(valeurs)
            au_dessus = 100.0 * sum(1 for v in valeurs if v > SEUIL_SECONDE_POURCENT) / len(valeurs)
            print(
                f"  Zone {nom}: moyenne={moyenne:5.1f}%  mediane={mediane:5.1f}%  "
                f"secondes>{SEUIL_SECONDE_POURCENT:.0f}%={au_dessus:5.1f}%  (n={len(valeurs)}s)"
            )
            recap[debut][nom] = moyenne

    if not recap:
        return

    print("\n--- Recapitulatif (moyenne % par zone) ---")
    noms_zones = list(zones)
    print("Instant              " + "".join(f"{n:>7}" for n in noms_zones))
    for debut, valeurs in recap.items():
        ligne = f"{debut:%Y-%m-%d %H:%M:%S}  "
        for nom in noms_zones:
            v = valeurs.get(nom)
            ligne += f"{v:6.1f} " if v is not None else "   -   "
        print(ligne)


if __name__ == "__main__":
    main()
