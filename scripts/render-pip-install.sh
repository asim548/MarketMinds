#!/usr/bin/env bash
# Render/Linux CI: PyPI often resolves torch to CUDA wheels via transformers/ultralytics deps.
# We install CPU torch twice — before and after requirements.txt — then drop stray NVIDIA wheels.
set -euo pipefail

CPU_IDX="https://download.pytorch.org/whl/cpu"
TORCH_VER="2.3.1"
TV_VER="0.18.1"

pip install --upgrade pip

echo "[render-pip] Installing CPU PyTorch ${TORCH_VER} (first pass)…"
pip install "torch==${TORCH_VER}" "torchvision==${TV_VER}" --index-url "$CPU_IDX"

echo "[render-pip] Installing requirements.txt…"
pip install -r requirements.txt

echo "[render-pip] Re-pin CPU PyTorch (prevents CUDA torch replacing CPU wheel)…"
pip install --force-reinstall --no-deps "torch==${TORCH_VER}" "torchvision==${TV_VER}" --index-url "$CPU_IDX"

echo "[render-pip] Removing stray NVIDIA CUDA wheels if present (CPU hosts only)…"
for pkg in \
  nvidia-nccl-cu12 \
  nvidia-cudnn-cu12 \
  nvidia-cuda-runtime-cu12 \
  nvidia-cuda-nvrtc-cu12 \
  nvidia-cuda-cupti-cu12 \
  nvidia-cublas-cu12 \
  nvidia-cufft-cu12 \
  nvidia-curand-cu12 \
  nvidia-cusolver-cu12 \
  nvidia-cusparse-cu12 \
  nvidia-nvjitlink-cu12 \
  nvidia-nvtx-cu12 \
  triton
do
  pip uninstall -y "$pkg" 2>/dev/null || true
done

python -c 'import torch; print("[render-pip] torch", torch.__version__, "cuda.is_available=", torch.cuda.is_available())'
