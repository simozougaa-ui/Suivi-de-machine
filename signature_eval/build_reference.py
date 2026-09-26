"""Construit une image de référence "machine vide" (médiane) à partir d'un
dossier de frames — ISOLÉ, n'écrit jamais dans `reference_fragments.png`
(le fichier de référence utilisé par la production, `src/fragment_detection.py`)
ni ailleurs dans le dépôt de production. Utilisé pour signature_eval/
uniquement (voir signature.py --reference).

Pourquoi un script séparé plutôt que réutiliser
`compute_reference_fragments.py` : cet utilitaire existant lit lui-même le
DVR et écrit TOUJOURS dans `reference_fragments.png` (le fichier de
production, importé depuis `src/fragment_detection.REFERENCE_FILE`) — le
relancer ici écraserait la référence de production. Ce script fait le
même calcul (médiane pixel par pixel) mais sur un dossier de frames déjà
extraites et vers un fichier de sortie arbitraire, choisi par
--sortie.

Usage :
    python3 signature_eval/build_reference.py \
        --frames-dir signature_eval/frames_dvr_2026-09-23_132100 \
        --sortie signature_eval/reference_dvr_2026-09-23.png
"""

import argparse
import glob
import os

import cv2
import numpy as np


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--sortie", required=True)
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.frames_dir, "*.jpg")))
    if not paths:
        raise SystemExit(f"Aucune image .jpg dans {args.frames_dir}")

    frames = [cv2.imread(p) for p in paths]
    frames = [f for f in frames if f is not None]
    median = np.median(np.stack(frames, axis=0), axis=0).astype(np.uint8)
    cv2.imwrite(args.sortie, median)
    print(f"Reference calculee sur {len(frames)} images, sauvegardee dans {args.sortie}", flush=True)
    print("Rappel : si le conducteur est present sur une grande partie des frames "
          "utilisees, il peut laisser une trace dans la mediane a cet endroit "
          "(zone convoyeur) - verifier visuellement le resultat avant de l'utiliser.",
          flush=True)


if __name__ == "__main__":
    main()
