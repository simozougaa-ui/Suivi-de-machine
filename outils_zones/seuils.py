"""Cherche, parmi plusieurs seuils de pixel, celui qui sépare le mieux
« marche » et « arrêt » sur plusieurs zones et plusieurs instants déjà
mesurés à la main. Outil de mesure LOCAL : n'envoie rien à l'application
de suivi, ne touche à aucun fichier de production (machine_etat/ reste
intact), et ne modifie ni zones.py ni zones_s10.py (seuils fixes 25 et 10
respectivement, laissés tels quels).

Constat (voir NOTES-SESSION.md) : zones_s10.py (seuil 10) donne de faux
« en mouvement » le soir sur la zone C (machine 2 à l'arrêt à 20:31, zone C
à 90-100 % de secondes en mouvement) ; zones.py (seuil 25) voit bien
l'arrêt mais risque de rater la marche de nuit. Cet outil teste plusieurs
seuils à la fois, SUR LES MÊMES LECTURES (une seule lecture par instant,
quel que soit le nombre de seuils demandés), pour trouver un seuil qui
sépare marche et arrêt à toutes les heures déjà mesurées.

Méthode IDENTIQUE à zones.py (réutilisée, pas reformulée) : gris, flou
gaussien (5,5) sur l'image ENTIÈRE, puis découpage de la zone ; amplitude
(max-min) par pixel sur les 15 images d'une seconde ; une seconde est « en
mouvement » si plus de SEUIL_SECONDE_POURCENT % des pixels de la zone
dépassent le seuil de pixel testé. Une fenêtre de --duree secondes est
« marche » si plus de 40 % de ses secondes sont en mouvement (même règle
que machine_etat/detecter_marche_arret.py).

Usage (seuils, zones et instants par défaut — 7 instants, ~15 min) :
    python3 outils_zones/seuils.py

Usage (seuils/zones/durée personnalisés) :
    python3 outils_zones/seuils.py --seuils 10,15,20 --zones C,D --duree 60

Usage (instants personnalisés, état optionnel 'marche'/'arret') :
    python3 outils_zones/seuils.py 2026-10-01 20:31:00 arret 2026-10-01 20:50:00 arret

Tourne longtemps (~2 min/instant, 7 instants par défaut) : voir
NOTES-SESSION.md pour les commandes nohup + consultation depuis un téléphone.
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
from zones import FLOU, FPS, SEUIL_SECONDE_POURCENT  # noqa: E402

SEUILS_DEFAUT = [10, 15, 18, 20, 25]
ZONES_DEFAUT = ["C", "D"]
SEUIL_MINUTE_POURCENT = 40.0  # même règle que machine_etat/detecter_marche_arret.py

INSTANTS_DEFAUT = [
    ("2026-09-30", "10:38:00", "marche"),
    ("2026-10-01", "03:56:00", "marche"),
    ("2026-10-01", "20:20:00", "marche"),
    ("2026-10-01", "14:45:00", "arret"),
    ("2026-10-01", "20:31:00", "arret"),
    ("2026-10-01", "20:50:00", "arret"),
    ("2026-10-01", "21:10:00", "arret"),
]

FICHIER_SORTIE = os.path.join(_commun.SORTIES_DIR, "seuils.txt")


def amplitude_zone(images_grises_floutees, zone):
    """Amplitude (max-min) par pixel de `zone`, calculée UNE FOIS sur les
    images fournies (déjà grises + floutées) — réutilisée ensuite pour
    chaque seuil testé (voir fraction_au_dessus), au lieu de relire le
    flux ou de refaire ce calcul une fois par seuil."""
    x1, y1, x2, y2 = zone
    pile = np.stack(images_grises_floutees, axis=0)[:, y1:y2, x1:x2].astype(np.int16)
    return pile.max(axis=0) - pile.min(axis=0)


def fraction_au_dessus(amplitude, seuil):
    """% de pixels de `amplitude` (déjà calculée par amplitude_zone) dont
    la valeur dépasse `seuil`."""
    return 100.0 * np.count_nonzero(amplitude > seuil) / amplitude.size


def analyser_instant(debut, duree_s, zones, seuils):
    """Lit `duree_s` secondes à pleine cadence depuis `debut`, UNE SEULE
    lecture du flux pour TOUS les seuils demandés. Retourne
    {nom_zone: {seuil: [fraction_par_seconde, ...]}}. S'arrête proprement
    (sans lever d'exception) si le flux se termine avant la durée demandée
    (fin d'enregistrement, timeout DVR en fin de période), comme zones.py."""
    fin = debut + timedelta(seconds=duree_s)
    url = build_rtsp_playback_url(debut, fin)
    capture = open_stream(url)
    resultats = {nom: {seuil: [] for seuil in seuils} for nom in zones}
    tampon = []
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
                    amplitude = amplitude_zone(tampon, zone)
                    for seuil in seuils:
                        resultats[nom][seuil].append(fraction_au_dessus(amplitude, seuil))
                tampon = []
    finally:
        capture.release()
    return resultats


def pourcentage_secondes_en_mouvement(fractions_par_seconde):
    """% de secondes « en mouvement » (fraction de pixels changés au-dessus
    de SEUIL_SECONDE_POURCENT) parmi les fractions fournies. None si aucune
    seconde complète n'a été lue."""
    if not fractions_par_seconde:
        return None
    au_dessus = sum(1 for f in fractions_par_seconde if f > SEUIL_SECONDE_POURCENT)
    return 100.0 * au_dessus / len(fractions_par_seconde)


def verdict(pourcentage):
    """« marche » si plus de SEUIL_MINUTE_POURCENT % des secondes sont en
    mouvement, « arret » sinon ; None si `pourcentage` est None (rien lu)."""
    if pourcentage is None:
        return None
    return "marche" if pourcentage > SEUIL_MINUTE_POURCENT else "arret"


def parser_instants(jetons):
    """Jetons de la ligne de commande -> liste de (datetime, etat_attendu ou
    None). Chaque instant est DATE HEURE suivi d'un ETAT optionnel
    ('marche' ou 'arret') : sans lui, l'instant est affiché mais ne compte
    pas dans les verdicts (état inconnu)."""
    instants = []
    i = 0
    while i < len(jetons):
        if i + 1 >= len(jetons):
            raise ValueError(f"argument incomplet après {jetons[i]!r} (attendu DATE HEURE [ETAT]).")
        date, heure = jetons[i], jetons[i + 1]
        i += 2
        try:
            debut = datetime.strptime(f"{date} {heure}", "%Y-%m-%d %H:%M:%S")
        except ValueError as exc:
            raise ValueError(f"date/heure invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM:SS") from exc
        etat = None
        if i < len(jetons) and jetons[i] in ("marche", "arret"):
            etat = jetons[i]
            i += 1
        instants.append((debut, etat))
    return instants


def construire_rapport(instants, donnees, seuils, noms_zones):
    """Construit le texte du tableau final — un bloc NARROW par (zone,
    seuil), lisible sur un écran de téléphone — avec verdicts par instant,
    résumé « X/N bons », et le meilleur couple zone+seuil à la fin.
    Retourne une chaîne unique (imprimée ET écrite dans seuils.txt)."""
    lignes = []
    meilleur = None  # (nb_correct, marge, nom_zone, seuil)
    total_notes = sum(1 for _, etat in instants if etat in ("marche", "arret"))

    for nom in noms_zones:
        for seuil in seuils:
            lignes.append(f"\n=== Zone {nom}, seuil {seuil} ===")
            nb_correct = 0
            faux = []
            marche_valeurs = []
            arret_valeurs = []

            for debut, etat_attendu in instants:
                etiquette = f"{debut:%m-%d %H:%M}  {(etat_attendu or '?'):<6}"
                resultats_instant = donnees.get(debut)
                pct = None
                if resultats_instant is not None:
                    pct = pourcentage_secondes_en_mouvement(resultats_instant[nom][seuil])

                if pct is None:
                    lignes.append(f"{etiquette}     -   (non lu)")
                    continue

                lignes.append(f"{etiquette}  {pct:5.1f}%")
                if etat_attendu not in ("marche", "arret"):
                    continue

                if etat_attendu == "marche":
                    marche_valeurs.append(pct)
                else:
                    arret_valeurs.append(pct)
                if verdict(pct) == etat_attendu:
                    nb_correct += 1
                else:
                    lignes[-1] += "  <- FAUX"
                    faux.append(f"{debut:%m-%d %H:%M}")

            if total_notes:
                resume = f"Verdict : {nb_correct}/{total_notes} bons"
                if faux:
                    resume += " - faux: " + ", ".join(faux)
                lignes.append(resume)

                if marche_valeurs and arret_valeurs:
                    marge = min(marche_valeurs) - max(arret_valeurs)
                    candidat = (nb_correct, marge, nom, seuil)
                    if meilleur is None or candidat[:2] > meilleur[:2]:
                        meilleur = candidat

    lignes.append("\n=== Meilleur couple ===")
    if meilleur:
        nb_correct, marge, nom, seuil = meilleur
        lignes.append(
            f"Zone {nom}, seuil {seuil} : {nb_correct}/{total_notes} bons, "
            f"marge {marge:+.1f}% (marche la plus faible - arret le pire)"
        )
    else:
        lignes.append("Aucun couple exploitable (pas assez d'instants avec état connu et lus).")

    return "\n".join(lignes) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("instants", nargs="*", metavar="DATE HEURE [ETAT]",
                         help="Instants personnalisés (sinon : les 7 instants par défaut)")
    parser.add_argument("--duree", type=int, default=60, help="Durée par instant, en secondes (défaut 60)")
    parser.add_argument("--seuils", default=None,
                         help="Seuils de pixel à comparer, séparés par des virgules (défaut 10,15,18,20,25)")
    parser.add_argument("--zones", default=None,
                         help="Zones à tester (noms de zones.json), séparées par des virgules (défaut C,D)")
    args = parser.parse_args()

    if args.instants:
        try:
            instants = parser_instants(args.instants)
        except ValueError as exc:
            parser.error(str(exc))
    else:
        instants = [
            (datetime.strptime(f"{d} {h}", "%Y-%m-%d %H:%M:%S"), etat)
            for d, h, etat in INSTANTS_DEFAUT
        ]

    seuils = [int(s) for s in args.seuils.split(",")] if args.seuils else list(SEUILS_DEFAUT)
    noms_zones = args.zones.split(",") if args.zones else list(ZONES_DEFAUT)

    toutes_zones = _commun.charger_zones()
    inconnues = [n for n in noms_zones if n not in toutes_zones]
    if inconnues:
        parser.error(f"zone(s) inconnue(s) dans zones.json : {', '.join(inconnues)}")
    zones = {n: toutes_zones[n] for n in noms_zones}

    print(f"{len(instants)} instant(s), {len(seuils)} seuil(s) {seuils}, zones {noms_zones}, "
          f"{args.duree}s/instant (~{len(instants) * args.duree // 60} min au total)")

    donnees = {}
    for i, (debut, etat_attendu) in enumerate(instants, start=1):
        print(f"[{i}/{len(instants)}] {debut:%Y-%m-%d %H:%M:%S} (attendu: {etat_attendu or '?'}) ...", flush=True)
        try:
            donnees[debut] = analyser_instant(debut, args.duree, zones, seuils)
        except ConnectionError as exc:
            print(f"  Erreur : {exc}", flush=True)
            continue
        n_secondes = len(donnees[debut][noms_zones[0]][seuils[0]])
        print(f"  lu : {n_secondes} seconde(s)", flush=True)

    rapport = construire_rapport(instants, donnees, seuils, noms_zones)
    print(rapport)

    os.makedirs(_commun.SORTIES_DIR, exist_ok=True)
    with open(FICHIER_SORTIE, "w", encoding="utf-8") as f:
        f.write(rapport)
    print(f"Tableau ecrit dans {FICHIER_SORTIE}")


if __name__ == "__main__":
    main()
