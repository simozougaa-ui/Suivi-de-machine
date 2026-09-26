# Résultats signature de couleur sur flux DVR réel — EN ATTENTE (2026-09-26)

Objectif : refaire le test de signature de couleur (voir `../README.md`
et `NOTES-SESSION.md`) sur des frames extraites **directement du flux
mainstream réel du DVR**, et non plus sur les captures d'écran DMSS
utilisées jusqu'ici, pour voir si une meilleure qualité source change la
fiabilité de la mesure (distances a,b, contamination du masque).

**Statut : n'a pas pu être exécuté** — pas d'accès réseau au DVR depuis
cet environnement (voir `NOTES-SESSION.md`, même section que ci-dessus).

## À lancer depuis le Jetson

```bash
cd ~/suivi-de-machine && git pull

# 1. Extraire des frames brutes directement du flux DVR (1 image/s)
python3 signature_eval/extract_frames_dvr.py \
    --date 2026-09-23 --debut 13:21:00 --duree 1500 --pas 1 \
    --out-dir signature_eval/frames_dvr_2026-09-23_132100

# 2. Construire une reference "machine vide" a partir de CES memes frames.
#    ATTENTION : ne PAS utiliser compute_reference_fragments.py ici, il
#    ecrit toujours dans reference_fragments.png (fichier de production,
#    voir src/fragment_detection.REFERENCE_FILE) - utiliser a la place le
#    script isole equivalent, qui ecrit vers un fichier au choix :
python3 signature_eval/build_reference.py \
    --frames-dir signature_eval/frames_dvr_2026-09-23_132100 \
    --sortie signature_eval/reference_dvr_2026-09-23.png

# 3. Repérer, dans la sortie de yolo_eval/eval_yolo.py sur la même plage
#    (voir yolo_eval/resultats_dvr_reel/), une image ou le conducteur est
#    visible ailleurs sur la machine (zone_machine >= 1 hors convoyeur) :
#    sert de --signature-image / --signature-box. A defaut, chercher a
#    l'oeil dans signature_eval/frames_dvr_2026-09-23_132100/.

# 4. Lancer signature.py comme precedemment, sur ce nouveau jeu de frames
python3 signature_eval/signature.py \
    --frames-dir signature_eval/frames_dvr_2026-09-23_132100 \
    --reference signature_eval/reference_dvr_2026-09-23.png \
    --signature-image <frame ou le conducteur est visible ailleurs> \
    --signature-box x1,y1,x2,y2 \
    --occluded-list <fichier .txt, frames convoyeur> \
    --out-dir signature_eval/out_dvr_reel

# 5. Copier les resultats pertinents ici (sans ecraser resultats_2026-09-26/)
cp signature_eval/out_dvr_reel/resume.json signature_eval/resultats_dvr_reel/
cp signature_eval/out_dvr_reel/*.jpg signature_eval/resultats_dvr_reel/
```

Comparer `resume.json` (distances intra-groupe, vs signature, vs témoin)
à celui obtenu sur les captures DMSS (`resultats_2026-09-26/resume.json`)
pour voir si la meilleure qualité source réduit le bruit de mesure.

**Limite à anticiper** : sur la plage du 2026-09-23, aucune image "conducteur
visible ailleurs sur la machine" n'a été identifiée pour l'instant (le seul
exemple trouvé jusqu'ici vient du jeu du 2026-09-05, DMSS). Il faudra peut-être
regarder plusieurs plages horaires du 2026-09-23 (ou une plage plus longue)
avant de trouver un tel moment ; sinon, ce test spécifique (étape 5 de la
demande) restera impossible à refaire sur DVR réel avec les données actuelles.
