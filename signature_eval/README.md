# Évaluation d'une signature de couleur vestimentaire (test, hors production)

Test **isolé** : est-ce qu'une couleur de vêtement dominante peut aider à
identifier le conducteur quand YOLO ne voit aucune silhouette (cas du
conducteur penché au convoyeur, voir `yolo_eval/`) ? Ne modifie aucun
fichier de production, ni `yolo_eval/`.

Résultats et recommandation complets : `NOTES-SESSION.md`, section
« Signature de couleur : conclusion ». Résumé image par image :
`resultats_2026-09-26/mesures.csv` et `resume.json`. Images annotées :
`resultats_2026-09-26/*.jpg`.

**Validation multi-jours en cours** (2026-09-27) : voir
`resultats_multi_jours/README.md` pour le protocole, les jours retenus et
l'état d'avancement (`signature_eval/prepare_day.sh` prépare tout pour un
jour donné, s'arrête avant `signature.py` en attente d'une vérification
visuelle humaine des candidats).

## Méthode

1. **Masque de premier plan** : différence par pixel avec une image de
   référence "machine vide" (même principe que la détection par
   fragments en production, mais utilisé ici pour isoler la couleur du
   vêtement, pas pour mesurer une intensité de mouvement).
2. **Signature** = couleur **médiane** des pixels du masque, en espace
   **Lab**.
3. **Comparaison** = distance euclidienne sur la **chrominance seule
   (a, b)**, en ignorant la luminance L (voir NOTES-SESSION.md : la
   luminance varie trop d'une image à l'autre — éclairage, reflets, pile
   de feuilles qui bouge aussi — pour être fiable ; la chrominance est
   nettement plus stable).

## Relancer ce test (aucun accès Jetson/DVR nécessaire, images fixes)

```bash
python3 signature_eval/signature.py \
  --frames-dir <dossier d'images caméra 15> \
  --reference <image "machine vide", même résolution> \
  --signature-image <fichier où le conducteur est visible ailleurs> \
  --signature-box x1,y1,x2,y2 \
  --occluded-list <fichier texte, un nom de fichier par ligne : images convoyeur> \
  --control-image <fichier avec un autre ouvrier, optionnel> \
  --control-box x1,y1,x2,y2 \
  --out-dir signature_eval/out
```

## Plusieurs images de référence (recommandé, mise à jour du 2026-09-26)

`signature.py` accepte maintenant `--signature-manifest <fichier.csv>`
(lignes `image,x1,y1,x2,y2`) à la place de `--signature-image`/
`--signature-box` : la signature retenue est alors la **médiane** des
couleurs mesurées sur chaque candidat, plus robuste qu'un seul exemple.
`select_candidates.py` génère ce manifeste automatiquement à partir d'un
`detections.csv` déjà produit par `yolo_eval/eval_yolo.py` :

```bash
python3 signature_eval/select_candidates.py \
  --detections yolo_eval/out/<tag>/detections.csv \
  --n 8 --sortie signature_eval/candidats_signature.csv \
  --motif-nom "frame_{hhmmss}.jpg"   # adapter au nommage des images utilisées

# Vérifier VISUELLEMENT chaque candidat (img_HHMMSS.jpg d'eval_yolo.py)
# avant de continuer : bien le même conducteur, pas un autre ouvrier/passant.

python3 signature_eval/signature.py \
  --frames-dir <dossier d'images> --reference <image "machine vide"> \
  --signature-manifest signature_eval/candidats_signature.csv \
  --occluded-list <fichier texte, images convoyeur> \
  --out-dir signature_eval/out
```

Pour l'appliquer à un enregistrement du Jetson : utiliser
`contact_sheet.py --depuis-dossier` (ou `yolo_eval/eval_yolo.py`) pour
produire un dossier `debug_frames/<date>_<heure>/frame_HHMMSS.jpg`, puis
pointer `--frames-dir` dessus.

## Limite importante de ce test

Une seule image "conducteur visible ailleurs sur la machine" a été
trouvée dans les 57 images disponibles (voir NOTES-SESSION.md pour le
détail de la recherche) : la signature de référence n'est donc pas
validée statistiquement, seulement testée comme piste de faisabilité.
