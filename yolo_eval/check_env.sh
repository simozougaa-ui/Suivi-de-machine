#!/usr/bin/env bash
# Inventaire de l'environnement du Jetson pour l'évaluation YOLO.
# Lecture seule : n'installe rien, ne modifie rien.
# Usage (depuis la racine du dépôt) : bash yolo_eval/check_env.sh | tee yolo_eval/env.txt

section() { echo; echo "=== $1 ==="; }

section "Systeme"
uname -a
cat /etc/os-release 2>/dev/null | grep -E '^(NAME|VERSION)='

section "L4T / JetPack"
cat /etc/nv_tegra_release 2>/dev/null || echo "/etc/nv_tegra_release absent"
dpkg -l 2>/dev/null | grep -E 'nvidia-jetpack |nvidia-l4t-core ' | awk '{print $2, $3}' || true

section "CUDA"
if command -v nvcc >/dev/null; then nvcc --version | tail -2; else ls -d /usr/local/cuda* 2>/dev/null || echo "nvcc introuvable"; fi

section "TensorRT"
dpkg -l 2>/dev/null | grep -E '^ii +(tensorrt|libnvinfer[0-9]|python3-libnvinfer) ' | awk '{print $2, $3}'
if [ -x /usr/src/tensorrt/bin/trtexec ]; then echo "trtexec : /usr/src/tensorrt/bin/trtexec"; else echo "trtexec introuvable"; fi
python3 -c "import tensorrt; print('module python tensorrt', tensorrt.__version__)" 2>/dev/null || echo "module python tensorrt non importable (python3 systeme)"

section "GPU"
cat /proc/device-tree/model 2>/dev/null; echo
command -v tegrastats >/dev/null && timeout 2 tegrastats --interval 1000 | head -1

section "Memoire / disque"
free -h | head -2
df -h "$HOME" | tail -1

section "PyTorch du venv d'evaluation (si deja cree)"
VENV="$(dirname "$0")/.venv/bin/python"
if [ -x "$VENV" ]; then
  "$VENV" - <<'EOF'
import torch
print("torch", torch.__version__, "| CUDA disponible :", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU :", torch.cuda.get_device_name(0), "| capacite", torch.cuda.get_device_capability(0))
else:
    print("-> inference YOLO via PyTorch sur CPU uniquement (voir README de yolo_eval)")
EOF
else
  echo "venv yolo_eval/.venv pas encore cree (lancer yolo_eval/setup_venv.sh)"
fi
