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

Traitement en LOTS (mise à jour du 2026-09-26) : la version initiale
chargeait toutes les images d'un coup en mémoire (`[cv2.imread(p) for p
in paths]`) PUIS en faisait une deuxième copie complète pour les empiler
(`np.stack`) — les deux copies coexistant un instant, l'empreinte
mémoire réelle est presque le double de la taille des fichiers. Sur 1440
images 1280x720 (~2,6 Mo/image décodée en mémoire, ~3,8 Go au total), ça
dépasse les 6,3 Gio libres constatés sur le Jetson -> tué par l'OOM
killer ("Killed").

Ce script ne garde plus en mémoire qu'un LOT d'images à la fois
(`--lot`, 100 par défaut ≈ 260 Mo, très large marge) : calcule la médiane
de chaque lot, puis la référence finale est la **médiane des médianes de
lot** (approximation standard du calcul en flux d'une médiane globale,
correcte si l'élément qui doit être filtré - ici le conducteur - reste
minoritaire DANS CHAQUE lot, ce qui est le cas avec 1440 images sur
24 minutes et un conducteur présent par intermittence). Avec `--lot`
supérieur ou égal au nombre total d'images, un seul lot est utilisé et le
résultat est la médiane EXACTE, identique à la version précédente (non
optimisée) à la précision de calcul près.

Usage :
    python3 signature_eval/build_reference.py \
        --frames-dir signature_eval/frames_dvr_2026-09-23_132100 \
        --sortie signature_eval/reference_dvr_2026-09-23.png \
        --lot 100
"""

import argparse
import glob
import os

import cv2
import numpy as np


def median_of_batches(paths, lot):
    """Calcule une médiane pixel par pixel en ne gardant que `lot` images
    en mémoire à la fois. Retourne (image_mediane_uint8, n_images_lues,
    n_lots)."""
    medianes = []
    n_lues = 0
    for i in range(0, len(paths), lot):
        batch_paths = paths[i:i + lot]
        batch = []
        for p in batch_paths:
            frame = cv2.imread(p)
            if frame is not None:
                batch.append(frame)
        if not batch:
            continue
        n_lues += len(batch)
        # np.median sur un seul lot (petit, tient largement en mémoire) ;
        # calcul en float32 (pas le défaut float64 de numpy) pour limiter
        # l'empreinte mémoire de l'étape de tri interne à np.median.
        batch_arr = np.stack(batch, axis=0).astype(np.float32)
        medianes.append(np.median(batch_arr, axis=0))
        del batch, batch_arr  # libère explicitement avant le lot suivant

    if not medianes:
        return None, 0, 0
    if len(medianes) == 1:
        # Un seul lot (ex. --lot >= nombre total d'images) : médiane exacte.
        return medianes[0].astype(np.uint8), n_lues, 1
    # Plusieurs lots : médiane des médianes (approximation documentée ci-dessus).
    finale = np.median(np.stack(medianes, axis=0), axis=0).astype(np.uint8)
    return finale, n_lues, len(medianes)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--sortie", required=True)
    ap.add_argument("--lot", type=int, default=100,
                    help="nombre d'images gardées en mémoire simultanément "
                         "(défaut 100, ~260 Mo pour du 1280x720) ; mettre un "
                         "nombre >= au total d'images pour une médiane exacte "
                         "si la mémoire le permet")
    args = ap.parse_args()

    paths = sorted(glob.glob(os.path.join(args.frames_dir, "*.jpg")))
    if not paths:
        raise SystemExit(f"Aucune image .jpg dans {args.frames_dir}")

    reference, n_lues, n_lots = median_of_batches(paths, args.lot)
    if reference is None:
        raise SystemExit("Aucune image lisible dans le dossier fourni.")

    cv2.imwrite(args.sortie, reference)
    methode = "mediane exacte (un seul lot)" if n_lots == 1 else f"mediane de {n_lots} medianes de lot (approximation)"
    print(f"Reference calculee sur {n_lues}/{len(paths)} images ({methode}), "
          f"sauvegardee dans {args.sortie}", flush=True)
    print("Rappel : si le conducteur est present sur une grande partie des frames "
          "utilisees, il peut laisser une trace dans la mediane a cet endroit "
          "(zone convoyeur) - verifier visuellement le resultat avant de l'utiliser.",
          flush=True)


if __name__ == "__main__":
    main()
