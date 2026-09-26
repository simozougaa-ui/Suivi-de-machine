# Signature de couleur, plusieurs références, sur flux DVR réel — EN ATTENTE (2026-09-26)

**Statut : n'a pas pu être exécuté.** Cette étape demandait de relancer
`signature.py` sur les 1440 images DVR réelles (2026-09-23 13:21-13:45)
et le `detections.csv` YOLO censés déjà exister dans
`signature_eval/resultats_dvr_reel/frames_extraites/` et
`yolo_eval/out/frames_extraites/`. **Ces fichiers sont introuvables** dans
cet environnement de développement : ni sur le disque local, ni dans le
dépôt (local ou `origin/main`, vérifié par `git fetch`). Voir
`NOTES-SESSION.md`, section « Signature multi-référence sur flux DVR
réel », pour le détail.

Raison probable : ces sorties (`yolo_eval/out/`,
`signature_eval/frames_dvr_*/`) sont volontairement dans `.gitignore`
(volumineuses, régénérées à la demande) — si elles ont été produites sur
le Jetson, elles y restent, elles ne remontent pas ici automatiquement.

## Ce qui a été préparé en attendant (testé, fonctionnel)

- `signature_eval/signature.py` accepte maintenant plusieurs images de
  référence (`--signature-manifest`), en plus du mode à une seule image
  (rétrocompatible - même résultat qu'avant sur les captures DMSS,
  revérifié).
- `signature_eval/select_candidates.py` sélectionne automatiquement des
  candidats "conducteur ailleurs, hors convoyeur" dans un
  `detections.csv` produit par `eval_yolo.py`, répartis dans le temps.
  Testé avec un CSV synthétique (voir NOTES-SESSION.md).

## À lancer depuis le Jetson, une fois les données disponibles

```bash
cd ~/suivi-de-machine && git pull

# 1. Sélection automatique des candidats "ailleurs, hors convoyeur"
python3 signature_eval/select_candidates.py \
  --detections yolo_eval/out/frames_extraites/detections.csv \
  --n 8 --sortie signature_eval/candidats_signature.csv \
  --motif-nom "frame_{hhmmss}.jpg"

# 2. Vérification VISUELLE de chaque candidat (obligatoire, voir la
#    demande initiale, étape 2) : ouvrir les img_HHMMSS.jpg correspondants
#    dans yolo_eval/out/frames_extraites/ et confirmer qu'il s'agit bien
#    du conducteur suivi, pas d'un autre ouvrier/passant. Retirer du
#    manifeste toute ligne douteuse.

# 3. Référence "machine vide" à partir des mêmes frames DVR réelles
python3 signature_eval/build_reference.py \
  --frames-dir signature_eval/resultats_dvr_reel/frames_extraites \
  --sortie signature_eval/reference_dvr_2026-09-23.png

# 4. Liste des 15 instants convoyeur déjà connus, avec les noms de
#    fichiers DVR réels (frame_HHMMSS.jpg) au lieu des captures DMSS
#    (adapter la liste des heures à celles déjà identifiées dans les
#    tests précédents, voir NOTES-SESSION.md)

# 5. Lancer le test avec plusieurs références
python3 signature_eval/signature.py \
  --frames-dir signature_eval/resultats_dvr_reel/frames_extraites \
  --reference signature_eval/reference_dvr_2026-09-23.png \
  --signature-manifest signature_eval/candidats_signature.csv \
  --occluded-list <liste des 15 instants convoyeur, frame_HHMMSS.jpg> \
  --out-dir signature_eval/out_dvr_reel_v2

# 6. Copier les résultats pertinents ici (sans écraser resultats_2026-09-26/
#    ni resultats_dvr_reel/)
cp signature_eval/out_dvr_reel_v2/resume.json signature_eval/resultats_dvr_reel_v2/
cp signature_eval/out_dvr_reel_v2/*.jpg signature_eval/resultats_dvr_reel_v2/
```

Comparer `resume.json` (notamment `distance_ab_intra_signature_*`,
nouveau : cohérence entre les candidats de référence eux-mêmes) à celui
de `resultats_2026-09-26/resume.json` (DMSS, une seule référence) pour
conclure si plusieurs références + images natives améliorent la
fiabilité.
