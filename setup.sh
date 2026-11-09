#!/usr/bin/env bash
# Local setup. Downloads ~300 MB of model weights; installs nothing system-wide.
set -euo pipefail
cd "$(dirname "$0")"
git submodule update --init --recursive
command -v uv >/dev/null || { echo "install uv: https://docs.astral.sh/uv/"; exit 1; }
uv venv -p 3.11 .venv
. .venv/bin/activate
uv pip install --index-strategy unsafe-best-match -r requirements.txt
uv pip install -e third_party/floorplan-to-3d --no-deps   # provides `buildingcv`
mkdir -p weights/floorplan-to-3d weights/cubicasa5k
[ -f weights/floorplan-to-3d/best.safetensors ] || curl -L -o weights/floorplan-to-3d/best.safetensors \
  https://huggingface.co/Yytsi/floorplan-to-3d-walls/resolve/main/best.safetensors
[ -f weights/floorplan-to-3d/config.yaml ] || curl -L -o weights/floorplan-to-3d/config.yaml \
  https://huggingface.co/Yytsi/floorplan-to-3d-walls/resolve/main/config.yaml
# CubiCasa5k trained weights (CC BY-NC 4.0, ~200 MB). Optional: the rooms stage is skipped without it.
[ -f weights/cubicasa5k/model_best_val_loss_var.pkl ] || gdown 1gRB7ez1e4H7a9Y09lLqRuna0luZO5VRK \
  -O weights/cubicasa5k/model_best_val_loss_var.pkl || echo "CubiCasa weights download failed (optional)"
echo "done. try: . .venv/bin/activate && python -m pytest -q tests"
