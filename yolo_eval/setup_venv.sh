#!/usr/bin/env bash
# Crée un environnement Python ISOLÉ pour l'évaluation YOLO (yolo_eval/.venv),
# sans toucher au venv du projet (.venv à la racine) ni aux services systemd.
# Usage (depuis la racine du dépôt) : bash yolo_eval/setup_venv.sh
set -euo pipefail
cd "$(dirname "$0")"

# --system-site-packages : donne accès au module python "tensorrt" installé
# par JetPack (apt), qui n'existe pas sur PyPI pour le Jetson. Les paquets
# installés ci-dessous par pip restent dans yolo_eval/.venv uniquement.
if [ ! -x .venv/bin/python ]; then
  python3 -m venv --system-site-packages .venv
fi
.venv/bin/pip install --upgrade pip
# ultralytics 8.4.162 = version avec laquelle les mesures de qualité de
# NOTES-SESSION.md ont été faites. Tire torch depuis PyPI : sur ce Jetson,
# ce torch ne voit pas le GPU (constaté le 2026-09-05 : "aucune build
# PyTorch CUDA ne supporte ce GPU") -> inférence PyTorch sur CPU.
.venv/bin/pip install "ultralytics==8.4.162" python-dotenv onnx onnxslim

# Poids : YOLO11n (choix justifié dans NOTES-SESSION.md).
.venv/bin/python -c "from ultralytics import YOLO; YOLO('yolo11n.pt')"

# Export ONNX sur CPU (ne dépend pas du GPU), taille 1280 (les personnes sont
# petites dans cette vue : à 640 elles ne sont quasiment plus détectées).
if [ ! -f yolo11n.onnx ]; then
  .venv/bin/yolo export model=yolo11n.pt format=onnx imgsz=1280
fi

# Moteur TensorRT FP16 avec l'outil de JetPack : ne passe pas par PyTorch,
# donc fonctionne même si torch ne voit pas le GPU.
TRTEXEC=/usr/src/tensorrt/bin/trtexec
if [ -x "$TRTEXEC" ] && [ ! -f yolo11n_1280_fp16.engine ]; then
  "$TRTEXEC" --onnx=yolo11n.onnx --saveEngine=yolo11n_1280_fp16.engine --fp16 \
    > trtexec_build.log 2>&1 && echo "Moteur TensorRT : yolo_eval/yolo11n_1280_fp16.engine" \
    || echo "ECHEC construction TensorRT, voir yolo_eval/trtexec_build.log"
elif [ ! -x "$TRTEXEC" ]; then
  echo "trtexec introuvable : pas de moteur TensorRT (voir check_env.sh)"
fi

echo "OK : environnement isolé prêt dans yolo_eval/.venv"
