# Évaluation YOLO isolée (caméra 15)

Test **isolé** d'un détecteur de personne YOLO. Ne modifie aucun fichier de
production (`main.py`, `src/fragment_detection.py`, `contact_sheet.py`...),
ni les services `suivi-presence` / `suivi-dashboard`, ni le venv du projet.
Tout reste dans ce dossier (`yolo_eval/.venv`, `yolo_eval/out/`).

Choix : **YOLO11n, imgsz 1280, conf 0.30** (justification et résultats
dans `NOTES-SESSION.md`, section « Évaluation YOLO »).

## Sur le Jetson (depuis `~/suivi-de-machine`)

```bash
git pull
bash yolo_eval/check_env.sh | tee yolo_eval/env.txt     # JetPack / CUDA / TensorRT (lecture seule)
bash yolo_eval/setup_venv.sh                             # venv isolé + yolo11n + ONNX + moteur TensorRT FP16
bash yolo_eval/bench_tensorrt.sh                         # FPS GPU pur (TensorRT)
yolo_eval/.venv/bin/python yolo_eval/eval_yolo.py --date 2026-09-23 --debut 13:21:00 --duree 1440
```

`eval_yolo.py` lit l'enregistrement du DVR (1 image/s), détecte les
personnes et écrit dans `yolo_eval/out/<date>_<HHMMSS>/` :
`resume.txt` (FPS, taux de détection, moments clés, taux de présence
zone convoyeur), `detections.csv` (une ligne par seconde, avec
`nb_zone_convoyeur`/`presence_convoyeur` en plus de `nb_zone_machine` —
voir NOTES-SESSION.md, « Zone convoyeur ajoutée à l'évaluation YOLO ») et
des images annotées `img_HHMMSS.jpg` (13:21:07/10/13/16, premières
détections dans la zone machine, une image de contrôle toutes les 30 s).
Cadre cyan = zone machine ; cadre magenta = zone convoyeur ; boîte rouge
= personne dans la zone machine ; magenta = personne dans la zone
convoyeur ; jaune = personne ailleurs.

Autre plage (avec passants) : changer `--debut`/`--duree`. Sans DVR :
`--depuis-dossier debug_frames/<dossier>`.

## Diagnostic "0 détection zone convoyeur" (2026-09-27)

`--diag-convoyeur` investigue si un 0/N sur la zone convoyeur vient d'un
vrai échec du modèle ou d'un biais du test (seuil de confiance trop
haut, ou boîte détectée débordant la zone sans que son centre y soit).
Voir la docstring de `eval_yolo.py` et `NOTES-SESSION.md`, section
« Diagnostic 0/57 zone convoyeur », pour la méthode et la conclusion
(les deux hypothèses ont été écartées par les données : c'est un vrai
échec du modèle sur cette pose/cet angle).

## Test YOLO-pose (préparation, 2026-09-27)

Hypothèse : un modèle **pose** (points-clés : nez, yeux, oreilles,
épaules, coudes, poignets, hanches, genoux, chevilles), où chaque
point-clé a sa propre confiance, est plus tolérant à l'occlusion
partielle que YOLO classique (silhouette entière) — un seul point-clé de
tête/épaule visible dans la zone convoyeur pourrait suffire à signaler
une présence, là où YOLO classique échoue (0/57, voir "Diagnostic 0/57
zone convoyeur"). Script : `yolo_eval/eval_pose.py` (n'importe, sans les
modifier, `frames_from_folder`/`in_zone`/`ZONE_CONVOYEUR`/`ZONE_MACHINE`/
`expand_zone` d'`eval_yolo.py`).

**IMPORTANT** : ce script a été écrit et testé dans l'environnement de
développement (aucun accès DVR/Jetson) uniquement sur des captures
DMSS dégradées, pour vérifier qu'il tourne sans erreur — **pas sur les
1440 frames DVR natives**. Aucun chiffre produit ici n'est un résultat
DVR. Voir `NOTES-SESSION.md`, section « Test YOLO-pose (préparation) »,
pour le détail et la marche à suivre exacte ci-dessous.

### Sur le Jetson

```bash
cd ~/suivi-de-machine && git pull

# 1. Poids pose (si pas déjà présent) — réutilise le venv existant
#    (yolo_eval/.venv, voir setup_venv.sh), ne le recrée pas.
yolo_eval/.venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo_eval/yolo11n-pose.pt')"

# 2. Vérité terrain "convoyeur" : réutiliser test_fragments_on_recording.py
#    (déjà utilisé pour signature_eval/, voir son README) sur la même plage
#    (2026-09-23 13:21-13:45), ou le journal déjà produit si disponible.
.venv/bin/python3 test_fragments_on_recording.py \
    --date 2026-09-23 --debut 13:21:00 --duree 1500 | tee /tmp/fragments_2026-09-23.log
python3 signature_eval/extract_convoyeur_instants.py \
    --log /tmp/fragments_2026-09-23.log --sortie yolo_eval/instants_presents.txt \
    --motif-nom "frame_{hhmmss}.jpg"
python3 yolo_eval/select_absent_instants.py \
    --log /tmp/fragments_2026-09-23.log --n 15 --sortie yolo_eval/instants_absents.txt \
    --motif-nom "frame_{hhmmss}.jpg"
# (si select_absent_instants.py ne trouve rien : construire
# yolo_eval/instants_absents.txt à la main, un frame_HHMMSS.jpg par ligne,
# en piochant des heures sans présence connue sur la même plage)

# 3. Lancer le test pose (~1440 images ; voir estimation de durée ci-dessous)
#    En option : arrêter/limiter suivi-presence pendant le test, le CPU du
#    Jetson est partagé (voir avertissement plus bas).
yolo_eval/.venv/bin/python yolo_eval/eval_pose.py \
    --depuis-dossier signature_eval/resultats_dvr_reel/frames_extraites \
    --instants-presents yolo_eval/instants_presents.txt \
    --instants-absents yolo_eval/instants_absents.txt \
    --classique-csv yolo_eval/out/frames_extraites/detections.csv

# 4. Voir les images annotees (points-cles colores par confiance) depuis
#    le telephone (Tailscale) - meme serveur que pour eval_yolo.py, sur un
#    port libre si 8001 est deja pris par une autre revue en cours :
python3 -m http.server 8002 --directory yolo_eval/out
# puis http://100.116.160.30:8002/frames_extraites_pose/
```

**Durée estimée sur CPU ARM du Jetson** : `eval_yolo.py` (YOLO11n
classique, imgsz 1280) mesurait ~101 ms/image sur le CPU de développement
ici (à confirmer sur le Jetson, plus lent). Le modèle pose est un peu plus
lourd (têtes de détection en plus) ; à titre indicatif ici (CPU de
développement, pas le Jetson) : ~150-165 ms/image. Sur 1440 images, cela
représente grossièrement 4 à 8 minutes selon le CPU réel du Jetson —
**en plus** de la charge déjà prise par `suivi-presence`, qui tourne en
continu sur le même CPU : un ralentissement temporaire du service de
production est possible pendant le test ; l'arrêter
(`sudo systemctl stop suivi-presence`) le temps du test si la machine
montre des signes de saturation (`tegrastats`), puis le relancer
(`sudo systemctl start suivi-presence`) une fois terminé.

Résultats dans `yolo_eval/out/frames_extraites_pose/` :
`keypoints_bruts.csv` (toutes les détections brutes, tous les points-clés,
permet de refaire n'importe quel calcul de seuil sans réinférer),
`detections_pose.csv` (une ligne par image, grille complète 4 critères ×
4 seuils × 3 marges = 48 colonnes oui/non), `resume_pose.txt`,
`comparaison_pose_vs_classique.csv`/`.txt` (taux de détection et de faux
positifs, pose vs YOLO classique, par variante), et des images annotées
`pose_present_HHMMSS.jpg`/`pose_absent_HHMMSS.jpg` pour chaque instant de
vérité terrain (points-clés en couleur rouge→vert selon la confiance,
zone convoyeur en magenta).

## Voir les images depuis le téléphone

```bash
python3 -m http.server 8001 --directory yolo_eval/out
```
puis ouvrir `http://100.116.160.30:8001/` (Tailscale). Ctrl+C pour arrêter.

## Limite connue

Le `torch` de PyPI installé par ultralytics ne voit pas le GPU Orin
(constaté le 2026-09-05) : `eval_yolo.py` tourne alors sur **CPU** (le
résumé l'indique). La vitesse GPU réelle est mesurée à part par
`bench_tensorrt.sh` (trtexec de JetPack, indépendant de torch).

## À renvoyer pour conclure

`yolo_eval/env.txt`, les 3 lignes de `bench_tensorrt.sh`,
`out/2026-09-23_132100/resume.txt`, et 2-3 captures des images annotées.
