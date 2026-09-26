#!/usr/bin/env bash
# Mesure le débit d'inférence GPU pur (TensorRT FP16, 1280x1280) du modèle
# YOLO11n sur ce Jetson, avec l'outil trtexec de JetPack (indépendant de
# PyTorch). C'est le plafond de performance GPU : sans décodage vidéo, ni
# pré/post-traitement Python.
# Usage (depuis la racine du dépôt) : bash yolo_eval/bench_tensorrt.sh
set -euo pipefail
cd "$(dirname "$0")"
ENGINE=yolo11n_1280_fp16.engine
[ -f "$ENGINE" ] || { echo "$ENGINE absent : lancer d'abord setup_venv.sh"; exit 1; }
/usr/src/tensorrt/bin/trtexec --loadEngine="$ENGINE" --iterations=300 --warmUp=2000 \
  | tee bench_tensorrt.log | grep -E "Throughput|Latency: min|GPU Compute Time: min"
echo "Detail complet : yolo_eval/bench_tensorrt.log (Throughput = images/s)"
