"""Sélectionne automatiquement des instants CONFIRMÉS ABSENTS au convoyeur
(vérité terrain négative, pour mesurer les faux positifs de eval_pose.py),
à partir du journal texte produit par `test_fragments_on_recording.py`
(script de production existant, réutilisé tel quel en lecture seule -
voir signature_eval/extract_convoyeur_instants.py pour l'équivalent côté
instants PRÉSENTS, qui parse le même journal).

Ne modifie ni test_fragments_on_recording.py, ni signature_eval/, ni
aucun fichier de production. Vit dans yolo_eval/ (pas de nouveau
dossier), car demandé par le test YOLO-pose de ce dossier.

Critère "absent confirmé" : ligne "absent" ET convoyeur=0.00 (aucun
mouvement détecté du tout dans la zone, pas seulement sous le seuil de
déclenchement - un ratio à 0.00 pile est le signal le plus net qu'il n'y
a personne). Réparti dans le temps (une par tranche), comme
select_candidates.py/extract_convoyeur_instants.py.

Si aucun journal n'est disponible (le repérer nécessite d'avoir déjà
lancé test_fragments_on_recording.py sur la même plage), utiliser
--instants-absents à la main dans eval_pose.py à la place (un
frame_HHMMSS.jpg par ligne, choisis visuellement).

Usage :
    python3 yolo_eval/select_absent_instants.py \
        --log signature_eval/resultats_multi_jours/<date>/fragments_log.txt \
        --n 15 --sortie yolo_eval/instants_absents.txt \
        --motif-nom "frame_{hhmmss}.jpg"
"""

import argparse
import re

LIGNE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\] absent .*convoyeur=0\.00 ?$")


def hhmmss_to_seconds(h):
    hh, mm, ss = (int(v) for v in h.split(":"))
    return hh * 3600 + mm * 60 + ss


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--n", type=int, default=15)
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--motif-nom", default="{heure}")
    args = ap.parse_args()

    candidats = []
    with open(args.log) as f:
        for line in f:
            m = LIGNE_RE.match(line.rstrip())
            if m:
                candidats.append(m.group(1))

    if not candidats:
        raise SystemExit(
            f"Aucune ligne 'absent ... convoyeur=0.00' trouvee dans {args.log}. "
            "Fournir --instants-absents a la main a eval_pose.py a la place."
        )

    t_min = hhmmss_to_seconds(candidats[0])
    t_max = hhmmss_to_seconds(candidats[-1])
    largeur = max(1, (t_max - t_min + 1) / args.n)
    retenus_par_tranche = {}
    for h in candidats:
        tranche = int((hhmmss_to_seconds(h) - t_min) / largeur)
        retenus_par_tranche.setdefault(tranche, h)  # garde le premier de la tranche
    retenus = sorted(retenus_par_tranche.values())[: args.n]

    with open(args.sortie, "w") as f:
        for h in retenus:
            hh, mi, s = h.split(":")
            f.write(args.motif_nom.format(heure=h, hhmmss=f"{hh}{mi}{s}") + "\n")

    print(f"{len(retenus)} instant(s) absent(s) retenu(s) sur {len(candidats)} candidats "
          f"-> {args.sortie}", flush=True)


if __name__ == "__main__":
    main()
