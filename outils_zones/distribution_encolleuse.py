"""Distribution, minute par minute, du % de secondes en mouvement de
l'Encolleuse 2 (caméra 10) sur une PÉRIODE connue (marche ou arrêt), pour
régler le seuil « marche » (aujourd'hui 12 %) sur des chiffres.

16/10 : la machine tourne PAR À-COUPS la nuit ; ses minutes les plus lentes
(8,3 % à 22:32) valent celles d'une pause (jusqu'à 8,3 %). Baisser le seuil
risque de faire passer des arrêts en marche. Cet outil compte, sur une
longue période, combien de minutes dépassent 8, 10, 12 et 15 % : sur une
période d'ARRÊT, ces minutes seraient de fausses « marche » ; sur une période
de MARCHE, celles qui restent en dessous sont des « arrêt » ratés.

Même lecture que la détection (lire_camera_supplementaire : mêmes zones, même
protection contre l'image détruite, même téléchargement HTTP) : les minutes
dont l'image est abîmée sont comptées à part (« non mesurées »). N'envoie rien
à l'application, ne touche à aucun curseur.

Usage :
    python3 outils_zones/distribution_encolleuse.py DATE DEBUT FIN [--etiquette TEXTE]

Exemple (pause de la nuit du 15 au 16/10) :
    python3 outils_zones/distribution_encolleuse.py 2026-10-16 01:00 01:30 --etiquette pause
"""

import argparse
import os
import statistics
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from machine_etat import detecter_marche_arret as dma  # noqa: E402

SEUILS = (8, 10, 12, 15)
MIN_SECONDES = 30          # même règle que classer() : en dessous, minute non mesurée


def pourcentages(par_minute, min_secondes=MIN_SECONDES):
    """% de secondes en mouvement de chaque minute mesurée (assez de secondes)."""
    return [100.0 * avec / total for _, (avec, total) in sorted(par_minute.items())
            if total >= min_secondes]


def mesurer(cam, debut, fin, lire=dma.lire_camera_supplementaire, fenetre_minutes=5):
    """Lit [debut, fin) par fenêtres de `fenetre_minutes`. Renvoie
    (liste des % par minute mesurée, nombre de minutes de la période)."""
    valeurs = []
    t = debut
    while t < fin:
        f = min(fin, t + timedelta(minutes=fenetre_minutes))
        par_minute, methode = lire(cam, t, f)
        if par_minute and methode != dma.SANS_ENREGISTREMENT:
            valeurs += pourcentages(par_minute)
        t = f
    return valeurs, int((fin - debut).total_seconds() // 60)


def resume(valeurs, total_minutes, etiquette=""):
    """Ligne de synthèse lisible sur un téléphone."""
    tete = f"{etiquette} : " if etiquette else ""
    if not valeurs:
        return f"{tete}aucune minute mesurée sur {total_minutes} (image abîmée ou DVR muet)"
    au_dessus = ", ".join(f"> {s} % : {sum(1 for v in valeurs if v > s)}" for s in SEUILS)
    return (f"{tete}{len(valeurs)} min mesurées sur {total_minutes} | % par minute : "
            f"médiane {statistics.median(valeurs):.1f}, max {max(valeurs):.1f} | "
            f"minutes {au_dessus}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("date", help="AAAA-MM-JJ")
    parser.add_argument("debut", help="HH:MM")
    parser.add_argument("fin", help="HH:MM (même journée)")
    parser.add_argument("--etiquette", default="", help="nom de la période (marche, pause...)")
    args = parser.parse_args()
    try:
        jour = datetime.strptime(args.date, "%Y-%m-%d").date()
        debut = datetime.combine(jour, datetime.strptime(args.debut, "%H:%M").time())
        fin = datetime.combine(jour, datetime.strptime(args.fin, "%H:%M").time())
    except ValueError as exc:
        parser.error(f"format invalide ({exc}). Attendu : AAAA-MM-JJ HH:MM HH:MM")
    if fin <= debut:
        parser.error("la fin doit être après le début (même journée).")
    cam = dma.CAMERAS_SUPPLEMENTAIRES[0]
    valeurs, total = mesurer(cam, debut, fin)
    print(f"{debut:%d/%m %H:%M}→{fin:%H:%M} " + resume(valeurs, total, args.etiquette))


if __name__ == "__main__":
    main()
