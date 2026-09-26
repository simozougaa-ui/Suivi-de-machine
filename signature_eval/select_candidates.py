"""Sélectionne automatiquement, dans un `detections.csv` déjà produit par
`yolo_eval/eval_yolo.py`, des candidats "conducteur visible ailleurs sur
la machine" (hors zone convoyeur) pour servir de signature de référence à
`signature_eval/signature.py --signature-manifest`.

Ne modifie ni eval_yolo.py ni signature.py (à part le support optionnel
de plusieurs images de référence, documenté dans signature.py). Ne touche
à aucun fichier de production.

Pourquoi refaire cette sélection ici plutôt qu'à la main comme la
première fois : eval_yolo.py classe une détection "zone_machine" si elle
tombe dans l'union des 4 zones de fragment_detection.py (tête, jambes,
pile, convoyeur) — donc une détection "zone_machine" peut très bien être
AU convoyeur, ce qui n'est pas ce qu'on veut ici (on veut "ailleurs sur
la machine", donc explicitement HORS convoyeur). Ce script relit les
boîtes détaillées de chaque ligne du CSV (colonne "boites"), garde
seulement celles dont le centre tombe dans la zone machine mais PAS dans
la zone convoyeur, puis répartit les candidats dans le temps (un par
tranche de la plage étudiée) pour éviter de choisir 10 images à la même
minute.

Usage :
    python3 signature_eval/select_candidates.py \
        --detections yolo_eval/out/<tag>/detections.csv \
        --n 8 --sortie signature_eval/candidats_signature.csv

Le fichier produit (une ligne "image,x1,y1,x2,y2" par candidat) est
prévu pour --signature-manifest de signature.py, mais utilise ici le nom
de fichier tel qu'il apparaît dans detections.csv (heure HH:MM:SS) : à
adapter en `frame_HHMMSS.jpg` si les images sont nommées ainsi (voir
--motif-nom).

IMPORTANT : la vérification visuelle demandée (étape 2 : s'assurer que
ces candidats montrent bien le conducteur suivi par ce projet, pas un
autre ouvrier/passant) reste à faire à l'œil sur les images annotées déjà
produites par eval_yolo.py (img_HHMMSS.jpg) pour chaque candidat retenu —
ce script ne le fait pas automatiquement.
"""

import argparse
import csv
import os

# Zones de production (fragment_detection.py), lues en dur ici (aucun
# import du code de production, comme dans signature.py) :
ZONE_MACHINE = (520, 60, 830, 400)   # union des 4 zones + marge (voir yolo_eval/eval_yolo.py)
ZONE_CONVOYEUR = (700, 150, 790, 250)


def in_zone(cx, cy, zone):
    x1, y1, x2, y2 = zone
    return x1 <= cx <= x2 and y1 <= cy <= y2


def parse_boites(cell):
    """Reparse la colonne 'boites' de detections.csv :
    "x1,y1,x2,y2:conf" ou "x1,y1,x2,y2:confM" (M = zone machine), plusieurs
    boîtes séparées par des espaces (voir yolo_eval/eval_yolo.py)."""
    out = []
    for tok in cell.split():
        coords, rest = tok.split(":")
        x1, y1, x2, y2 = (int(v) for v in coords.split(","))
        conf = float(rest.rstrip("M"))
        out.append((x1, y1, x2, y2, conf))
    return out


def hhmmss_to_seconds(h):
    hh, mm, ss = (int(v) for v in h.split(":"))
    return hh * 3600 + mm * 60 + ss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detections", required=True, help="detections.csv produit par eval_yolo.py")
    ap.add_argument("--n", type=int, default=8, help="nombre de candidats à retenir (5-10 recommandé)")
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--motif-nom", default="{heure}",
                    help="motif pour le nom de fichier image associé à chaque heure "
                         "(remplace {heure}=HH:MM:SS et {hhmmss}=HHMMSS) ; défaut : le "
                         "nom tel quel dans detections.csv (souvent déjà une heure). "
                         "Pour des frames nommées frame_HHMMSS.jpg, utiliser "
                         "'frame_{hhmmss}.jpg'.")
    args = ap.parse_args()

    candidats = []
    with open(args.detections) as f:
        r = csv.DictReader(f)
        for row in r:
            for (x1, y1, x2, y2, conf) in parse_boites(row["boites(x1,y1,x2,y2:conf, M=zone machine)"]):
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                if in_zone(cx, cy, ZONE_MACHINE) and not in_zone(cx, cy, ZONE_CONVOYEUR):
                    candidats.append({
                        "heure": row["heure"], "box": (x1, y1, x2, y2), "conf": conf,
                        "secondes": hhmmss_to_seconds(row["heure"]),
                    })

    if not candidats:
        raise SystemExit(
            "Aucune détection 'ailleurs sur la machine, hors convoyeur' trouvée dans "
            f"{args.detections}. Impossible de constituer un manifeste de signature ; "
            "voir NOTES-SESSION.md pour la suite à donner (élargir la plage étudiée ?)."
        )

    # Répartition dans le temps : découpe la plage couverte en args.n
    # tranches égales, garde la détection la plus confiante de chaque
    # tranche (une seule par tranche, pour éviter 10 candidats à la même
    # minute qui n'apportent rien de plus qu'un seul).
    t_min = min(c["secondes"] for c in candidats)
    t_max = max(c["secondes"] for c in candidats)
    largeur = max(1, (t_max - t_min + 1) / args.n)

    meilleurs = {}
    for c in candidats:
        tranche = int((c["secondes"] - t_min) / largeur)
        if tranche not in meilleurs or c["conf"] > meilleurs[tranche]["conf"]:
            meilleurs[tranche] = c

    retenus = sorted(meilleurs.values(), key=lambda c: c["secondes"])[: args.n]

    with open(args.sortie, "w", newline="") as f:
        for c in retenus:
            h, mi, s = c["heure"].split(":")
            nom = args.motif_nom.format(heure=c["heure"], hhmmss=f"{h}{mi}{s}")
            x1, y1, x2, y2 = c["box"]
            f.write(f"{nom},{x1},{y1},{x2},{y2}\n")
            print(f"{c['heure']} conf={c['conf']:.2f} boite=({x1},{y1},{x2},{y2}) -> {nom}", flush=True)

    print(f"\n{len(retenus)} candidat(s) retenu(s) sur {len(candidats)} détections "
          f"'ailleurs, hors convoyeur' -> {args.sortie}", flush=True)
    print("Vérifier VISUELLEMENT chaque candidat (images annotées d'eval_yolo.py, "
          "img_HHMMSS.jpg) avant de les utiliser comme signature : s'assurer qu'il "
          "s'agit bien du même conducteur, pas d'un autre ouvrier/passant.", flush=True)


if __name__ == "__main__":
    main()
