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
`resume.txt` (FPS, taux de détection, moments clés), `detections.csv`
(une ligne par seconde) et des images annotées `img_HHMMSS.jpg`
(13:21:07/10/13/16, premières détections dans la zone machine, une image
de contrôle toutes les 30 s). Cadre cyan = zone machine ; boîte rouge =
personne dans la zone machine ; jaune = personne ailleurs.

Autre plage (avec passants) : changer `--debut`/`--duree`. Sans DVR :
`--depuis-dossier debug_frames/<dossier>`.

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
