"""Carte de mouvement d'une région d'une caméra : pour choisir la zone de
détection d'une NOUVELLE machine sans deviner les pixels (16/10 : Encolleuse 2
sur la caméra 10). Outil de mesure LOCAL : n'envoie rien à l'application de
suivi, ne touche à aucun fichier de production.

Lit --duree secondes (60 par défaut) par TÉLÉCHARGEMENT HTTP, comme la
détection (rapide et complet ; 16/10 : en RTSP, seules 10 s sur 20 avaient
été lues), avec repli sur la relecture RTSP si le téléchargement échoue.
Méthode IDENTIQUE à zones.py (gris,
flou gaussien (5,5), amplitude max-min par pixel sur les 15 images d'une
seconde, seuil de pixel 25). Pour chaque pixel de la région, compte les
secondes où il a bougé, puis dessine la région agrandie avec :
  - en ROUGE les pixels en mouvement au moins 40 % des secondes (ce qui
    bouge en continu : la machine qui tourne) ;
  - en ORANGE ceux en mouvement 10 à 40 % du temps (passages, gestes) ;
  - une grille tous les 10 px et les coordonnées de l'image source tous les
    40 px, utilisables telles quelles pour une zone (x1, y1, x2, y2).

Avec --zones « x1,y1,x2,y2;x1,y1,x2,y2;... » : chaque zone candidate est
dessinée en BLEU (numérotée) et mesurée avec la règle de la détection de
la machine 2 (Bob C) : une seconde est « en mouvement » si plus de 5 % des
pixels de la zone dépassent l'amplitude 25 ; la période est « marche » si
plus de 40 % des secondes sont en mouvement. À lancer sur une heure de
marche ET une heure d'arrêt : la bonne zone est celle qui sépare les deux.

Avec --arret DATE HEURE (16/10, réglage de NUIT de l'Encolleuse 2) : carte de
COMPARAISON entre l'heure donnée (machine EN MARCHE) et une heure où elle est
À L'ARRÊT, la même nuit de préférence. En ROUGE ce qui bouge nettement plus
souvent en marche qu'à l'arrêt (écart >= --ecart points de % des secondes,
15 par défaut : une machine lente ou par à-coups ne bouge jamais 40 % du
temps ; si aucun pixel n'est rouge, baisser --ecart, l'écart maximal trouvé
est affiché) ; en JAUNE ce qui
bouge sans faire la différence (bruit de nuit, passages). Les 3 rectangles
40x20 au plus grand écart moyen sont proposés en VERT avec leurs
coordonnées, et toutes les zones (--zones et proposées) sont mesurées aux
deux heures.

Usage :
    python3 outils_zones/carte_mouvement.py DATE HEURE --region X1,Y1,X2,Y2 [--canal N] [--duree 60] [--zones Z1;Z2] [--arret DATE HEURE]

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
from zones import FLOU, FPS, SEUIL_PIXEL, SEUIL_SECONDE_POURCENT  # noqa: E402
from machine_etat import lecture_dvr  # noqa: E402

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


BLEU = (255, 128, 0)


def lire_zones(texte):
    """« x1,y1,x2,y2;x1,y1,x2,y2 » -> [(x1, y1, x2, y2), ...]."""
    zones = [lire_region(z.strip()) for z in texte.split(";") if z.strip()]
    if not zones:
        raise argparse.ArgumentTypeError("au moins une zone attendue")
    return zones


def mesurer_zone(masques, region, zone):
    """Règle de la détection (machine 2) appliquée à `zone` (coordonnées de
    l'image source, incluse dans `region`). Renvoie (% de secondes en
    mouvement, % moyen de pixels en mouvement, verdict)."""
    rx1, ry1, rx2, ry2 = region
    x1, y1, x2, y2 = zone
    if x1 < rx1 or y1 < ry1 or x2 > rx2 or y2 > ry2:
        raise ValueError(f"zone {zone} hors de la région {region}")
    fractions = [100.0 * float(m[y1 - ry1:y2 - ry1, x1 - rx1:x2 - rx1].mean()) for m in masques]
    en_mouvement = sum(1 for f in fractions if f > SEUIL_SECONDE_POURCENT)
    pct = 100.0 * en_mouvement / len(fractions)
    return pct, sum(fractions) / len(fractions), ("marche" if pct > 100 * FRACTION_CONTINU else "arret")


def dessiner_zones(carte, region, zones, zoom=ZOOM):
    rx1, ry1 = region[0], region[1]
    for i, (x1, y1, x2, y2) in enumerate(zones, 1):
        a = ((x1 - rx1) * zoom, (y1 - ry1) * zoom)
        b = ((x2 - rx1) * zoom, (y2 - ry1) * zoom)
        cv2.rectangle(carte, a, b, BLEU, 2)
        cv2.putText(carte, str(i), (a[0] + 3, a[1] - 4), cv2.FONT_HERSHEY_SIMPLEX,
                    0.7, BLEU, 2, cv2.LINE_AA)
    return carte


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


def images_par_seconde(capture, defaut=FPS):
    """Cadence annoncée par le fichier/flux, arrondie (16/10 : la caméra 10
    n'enregistre peut-être pas à 15 img/s comme la caméra 15 ; avec 15 fixe,
    60 s de vidéo à 2,5 img/s passaient pour 10 s). `defaut` si la valeur
    est absente ou absurde."""
    try:
        fps = float(capture.get(cv2.CAP_PROP_FPS))
    except Exception:  # noqa: BLE001
        return defaut
    return int(round(fps)) if 1 <= fps <= 60 else defaut


def lire_masques(capture, region, max_images, pas=FPS):
    """Lit jusqu'à `max_images` images de `capture`, par paquets de `pas`
    images (une seconde de vidéo). Renvoie (première image, [masque par
    seconde complète]). Une dernière seconde incomplète est ignorée. Lève
    ValueError si la région sort de l'image."""
    premiere, tampon, masques = None, [], []
    for _ in range(max_images):
        ret, frame = capture.read()
        if not ret or frame is None:
            break
        if premiere is None:
            premiere = frame
            h, w = frame.shape[:2]
            if region[2] > w or region[3] > h:
                raise ValueError(f"région hors de l'image ({w}x{h})")
        tampon.append(cv2.GaussianBlur(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), FLOU, 0))
        if len(tampon) == pas:
            masques.append(masque_seconde(tampon, region))
            tampon = []
    return premiere, masques


def lire_periode(debut, duree, region):
    """Téléchargement HTTP de la période puis lecture locale ; repli RTSP si
    le téléchargement échoue. Renvoie (première image, masques, méthode)."""
    fin = debut + timedelta(seconds=duree)
    try:
        with lecture_dvr.fenetre_telechargee(debut, fin) as (chemin, octets):
            capture, chemin_lu = lecture_dvr.ouvrir_fichier(chemin)
            try:
                pas = images_par_seconde(capture)
                total = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                print(f"Fichier : {octets / (1024 * 1024):.1f} Mo, {pas} images/s annoncées, "
                      f"{total or '?'} images" +
                      (f" (≈ {total / pas:.0f} s de vidéo)" if total else "") + ".")
                premiere, masques = lire_masques(capture, region, duree * pas + pas, pas)
            finally:
                capture.release()
                if chemin_lu != chemin:
                    lecture_dvr._supprimer(chemin_lu)
        return premiere, masques, "http"
    except (lecture_dvr.ErreurTelechargement, lecture_dvr.ErreurLectureFichier,
            lecture_dvr.ErreurPasDEnregistrement) as e:
        print(f"Téléchargement HTTP impossible ({lecture_dvr.masquer(e)}) : relecture RTSP, plus lente.")
    capture = open_stream(build_rtsp_playback_url(debut, fin))
    try:
        pas = images_par_seconde(capture)
        print(f"Flux RTSP : {pas} images/s annoncées.")
        premiere, masques = lire_masques(capture, region, duree * pas + pas, pas)
    finally:
        capture.release()
    return premiere, masques, "rtsp"


VERT = (0, 200, 0)
JAUNE = (0, 220, 255)
TAILLE_ZONE_PROPOSEE = (40, 20)


# Nuit du 15-16/10 : l'Encolleuse 2 tourne lentement / par à-coups, aucun
# pixel ne bouge 40 % du temps. Le rouge et les propositions se fondent donc
# sur l'ÉCART entre marche et arrêt (part des secondes en mouvement en marche
# moins à l'arrêt), pas sur le seuil des 40 % de Bob B / Bob C.
ECART_ROUGE = 0.15


def ecart_marche_arret(f_marche, f_arret):
    """Écart (0-1) entre la part des secondes où chaque pixel bouge en
    marche et à l'arrêt ; 0 si le pixel bouge autant (ou plus) à l'arrêt."""
    return np.clip(f_marche - f_arret, 0.0, 1.0)


def masque_discriminant(f_marche, f_arret, ecart_min=ECART_ROUGE):
    """Pixels qui bougent nettement plus souvent en marche qu'à l'arrêt."""
    return ecart_marche_arret(f_marche, f_arret) >= ecart_min


def proposer_zones(score, region, taille=TAILLE_ZONE_PROPOSEE, n=3):
    """Les `n` rectangles `taille` (sans chevauchement) au score moyen le plus
    élevé (`score` : booléen ou écart 0-1 par pixel). Renvoie
    [((x1, y1, x2, y2), score moyen 0-1), ...] en coordonnées de la source."""
    w, h = taille
    H, W = score.shape
    if H < h or W < w:
        return []
    ii = cv2.integral(score.astype(np.float64))
    dens = (ii[h:, w:] - ii[:-h, w:] - ii[h:, :-w] + ii[:-h, :-w]) / float(w * h)
    choisies = []
    for _ in range(n):
        y, x = np.unravel_index(int(np.argmax(dens)), dens.shape)
        if dens[y, x] <= 1e-9:
            break
        choisies.append(((region[0] + x, region[1] + y, region[0] + x + w, region[1] + y + h),
                         float(dens[y, x])))
        dens[max(0, y - h + 1):y + h, max(0, x - w + 1):x + w] = -1
    return choisies


def dessiner_comparaison(image, region, discriminant, f_marche, zoom=ZOOM, titre=""):
    """Comme dessiner_carte, mais : rouge = discriminant, jaune = bouge aussi
    à l'arrêt (ou trop peu en marche)."""
    x1, y1, x2, y2 = region
    vue = image[y1:y2, x1:x2].copy()
    calque = vue.copy()
    calque[(f_marche >= FRACTION_PASSAGE) & ~discriminant] = JAUNE
    calque[discriminant] = ROUGE
    vue = cv2.addWeighted(calque, 0.6, vue, 0.4, 0)
    vue = cv2.resize(vue, None, fx=zoom, fy=zoom, interpolation=cv2.INTER_NEAREST)
    # Même grille et mêmes coordonnées que dessiner_carte (sans recolorer).
    vide = np.zeros_like(f_marche)
    grille = dessiner_carte(np.zeros_like(image), region, vide, zoom, titre)
    masque = grille.any(axis=2)
    vue[masque] = grille[masque]
    return vue


def nom_sortie_comparaison(canal, debut, debut_arret):
    nom = "carte_comparaison" + (f"_canal{canal}" if canal is not None else "") + \
        f"_{debut:%Y%m%d_%H%M}_vs_{debut_arret:%Y%m%d_%H%M}.jpg"
    return os.path.join(_commun.SORTIES_DIR, nom)


def nom_sortie(canal, debut):
    """Un fichier par caméra ET par heure : la carte « en marche » et la
    carte « à l'arrêt » ne s'écrasent pas, pour les comparer."""
    nom = "carte_mouvement" + (f"_canal{canal}" if canal is not None else "") + \
        f"_{debut:%Y%m%d_%H%M}.jpg"
    return os.path.join(_commun.SORTIES_DIR, nom)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date")
    parser.add_argument("heure")
    parser.add_argument("--region", type=lire_region, required=True, help="X1,Y1,X2,Y2 dans l'image source")
    parser.add_argument("--canal", type=int, help="caméra du DVR (défaut : CAMERA_CHANNEL du .env)")
    parser.add_argument("--duree", type=int, default=60, help="secondes lues (défaut 60)")
    parser.add_argument("--zones", type=lire_zones, default=[],
                        help="zones candidates « x1,y1,x2,y2;x1,y1,x2,y2 » à mesurer et dessiner")
    parser.add_argument("--arret", nargs=2, metavar=("DATE", "HEURE"),
                        help="heure où la machine est À L'ARRÊT : carte de comparaison")
    parser.add_argument("--ecart", type=float, default=ECART_ROUGE * 100,
                        help="écart minimal (points de %% des secondes, marche moins arrêt) pour "
                             "colorer un pixel en ROUGE (défaut 15 ; baisser pour une machine lente)")
    args = parser.parse_args()
    for z in args.zones:
        if z[0] < args.region[0] or z[1] < args.region[1] or z[2] > args.region[2] or z[3] > args.region[3]:
            parser.error(f"zone {z} hors de la région {args.region}")
    if args.canal is not None:
        os.environ["CAMERA_CHANNEL"] = str(args.canal)   # ce lancement seulement
    try:
        debut = datetime.strptime(f"{args.date} {args.heure}", "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        parser.error(f"format de date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")

    print(f"Lecture de {args.duree} s à partir de {debut:%Y-%m-%d %H:%M:%S}...")
    try:
        premiere, masques, methode = lire_periode(debut, args.duree, args.region)
    except ValueError as e:
        sys.exit(f"Erreur : {e}.")
    if premiere is None or not masques:
        sys.exit("Aucune seconde complète lue (DVR indisponible ou pas d'enregistrement à cette heure).")

    canal = args.canal if args.canal is not None else os.getenv("CAMERA_CHANNEL", "?")
    if args.arret:
        try:
            debut_arret = datetime.strptime(" ".join(args.arret), "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            parser.error(f"--arret : format invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS")
        print(f"Lecture de l'arrêt : {args.duree} s à partir de {debut_arret:%Y-%m-%d %H:%M:%S}...")
        try:
            _, masques_arret, _ = lire_periode(debut_arret, args.duree, args.region)
        except ValueError as e:
            sys.exit(f"Erreur : {e}.")
        if not masques_arret:
            sys.exit("Aucune seconde complète lue à l'heure d'arrêt.")
        f_marche = fraction_par_pixel(masques)
        f_arret = fraction_par_pixel(masques_arret)
        ecart = ecart_marche_arret(f_marche, f_arret)
        discriminant = masque_discriminant(f_marche, f_arret, args.ecart / 100.0)
        print(f"Écart maximal marche - arrêt : {100 * float(ecart.max()):.0f} points "
              f"(rouge = écart >= {args.ecart:g} points ; "
              f"{100 * float(discriminant.mean()):.1f} % de la région en rouge).")
        carte = dessiner_comparaison(
            premiere, args.region, discriminant, f_marche,
            titre=f"Camera {canal} - marche {debut:%d/%m %H:%M} vs arret {debut_arret:%d/%m %H:%M}")
        proposees = proposer_zones(ecart_marche_arret(f_marche, f_arret), args.region)
        dessiner_zones(carte, args.region, args.zones)
        rx1, ry1 = args.region[0], args.region[1]
        for i, (z, _) in enumerate(proposees, 1):
            cv2.rectangle(carte, ((z[0] - rx1) * ZOOM, (z[1] - ry1) * ZOOM),
                          ((z[2] - rx1) * ZOOM, (z[3] - ry1) * ZOOM), VERT, 2)
            cv2.putText(carte, f"P{i}", ((z[0] - rx1) * ZOOM + 3, (z[1] - ry1) * ZOOM - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, VERT, 2, cv2.LINE_AA)
        if not proposees:
            print("Aucun pixel ne bouge plus souvent en marche qu'à l'arrêt.")
        a_mesurer = [(f"zone {i}", z) for i, z in enumerate(args.zones, 1)] + \
            [(f"P{i} (proposée, écart {100 * d:.0f})", z) for i, (z, d) in enumerate(proposees, 1)]
        print(f"Zones mesurées (marche {debut:%d/%m %H:%M} / arrêt {debut_arret:%d/%m %H:%M}) :")
        for nom, z in a_mesurer:
            pm, _, vm = mesurer_zone(masques, args.region, z)
            pa, _, va = mesurer_zone(masques_arret, args.region, z)
            bon = "OK" if (vm, va) == ("marche", "arret") else "NON"
            print(f"  {nom} {z[0]},{z[1]},{z[2]},{z[3]} : marche {pm:5.1f} % -> {vm} ; "
                  f"arrêt {pa:5.1f} % -> {va}  [{bon}]")
        sortie = nom_sortie_comparaison(args.canal, debut, debut_arret)
        os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
        cv2.imwrite(sortie, carte)
        print(f"OK : {sortie}")
        return

    carte = dessiner_carte(premiere, args.region, fraction_par_pixel(masques),
                           titre=f"Camera {canal} - {debut:%Y-%m-%d %H:%M:%S} - {len(masques)} s")
    if args.zones:
        dessiner_zones(carte, args.region, args.zones)
        print(f"Zones (seconde en mouvement si > {SEUIL_SECONDE_POURCENT:g} % de la zone bouge ; "
              f"marche si > {100 * FRACTION_CONTINU:g} % des secondes) :")
        for i, z in enumerate(args.zones, 1):
            pct, moyen, verdict = mesurer_zone(masques, args.region, z)
            print(f"  zone {i} {z[0]},{z[1]},{z[2]},{z[3]} : {pct:5.1f} % des secondes en mouvement, "
                  f"{moyen:5.1f} % de pixels en moyenne -> {verdict}")
    sortie = nom_sortie(args.canal, debut)
    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    cv2.imwrite(sortie, carte)
    print(f"OK : {sortie} ({len(masques)} s analysées, lecture {methode})")


if __name__ == "__main__":
    main()
