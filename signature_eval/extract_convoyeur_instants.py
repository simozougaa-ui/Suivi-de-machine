"""Extrait, depuis le journal texte produit par `test_fragments_on_recording.py`
(sortie standard redirigée dans un fichier), les instants où la zone
convoyeur a été comptée ("triggered", marquée `*` — voir la convention
déjà documentée dans NOTES-SESSION.md pour ce script de production
existant, non modifié ici).

Ne modifie ni test_fragments_on_recording.py ni aucun fichier de
production : lit seulement un fichier texte déjà produit par ce script,
en lecture seule.

Ligne attendue, par seconde :
    [13:21:07] PRESENT tete=0.02  jambes=0.01  pile=0.00  convoyeur=0.21*

Usage :
    python3 signature_eval/extract_convoyeur_instants.py \
        --log signature_eval/resultats_multi_jours/2026-09-15/fragments_log.txt \
        --sortie signature_eval/resultats_multi_jours/2026-09-15/convoyeur_instants.txt \
        --motif-nom "frame_{hhmmss}.jpg"

Par défaut (`--motif-nom` omis), écrit l'heure brute HH:MM:SS, une par
ligne. Avec `--motif-nom` (même convention que select_candidates.py),
écrit directement les noms de fichiers correspondants — c'est le format
attendu par `signature.py --occluded-list` quand les images sont nommées
`frame_HHMMSS.jpg` (extraction directe du DVR, pas `debug_frames/`).
"""

import argparse
import re

LIGNE_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]")
CONVOYEUR_RE = re.compile(r"convoyeur=([0-9.]+)(\*|~|\s)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", required=True)
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--motif-nom", default="{heure}",
                    help="motif de nom de fichier, {heure}=HH:MM:SS et {hhmmss}=HHMMSS "
                         "(defaut : heure brute, une par ligne)")
    args = ap.parse_args()

    instants = []
    with open(args.log) as f:
        for line in f:
            m_heure = LIGNE_RE.match(line)
            m_conv = CONVOYEUR_RE.search(line)
            if not m_heure or not m_conv:
                continue
            ratio, marqueur = m_conv.groups()
            if marqueur == "*":
                instants.append(m_heure.group(1))

    with open(args.sortie, "w") as f:
        for h in instants:
            hh, mi, s = h.split(":")
            f.write(args.motif_nom.format(heure=h, hhmmss=f"{hh}{mi}{s}") + "\n")

    print(f"{len(instants)} instant(s) 'convoyeur=...*' trouvés dans {args.log} "
          f"-> {args.sortie}", flush=True)
    if not instants:
        print("Aucun instant trouvé : vérifier que le conducteur est bien passé "
              "au convoyeur sur cette plage (sinon en choisir une autre), ou que "
              "le journal contient bien les lignes par seconde attendues.", flush=True)


if __name__ == "__main__":
    main()
