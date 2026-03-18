#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
python -m pip install -r requirements-lite.txt
python -m pip install pyinstaller

# 默认 onefile；如需 onedir，传 --onedir
MODE="--onefile"
if [[ "${1:-}" == "--onedir" ]]; then
  MODE=""
fi

python -m PyInstaller \
  ${MODE} \
  --noconfirm --clean --windowed \
  --name yolo-viewer-lite \
  --exclude-module cv2 \
  --exclude-module numpy \
  --exclude-module ultralytics \
  --exclude-module torch \
  --exclude-module torchvision \
  --exclude-module onnxruntime \
  --exclude-module pandas \
  --exclude-module matplotlib \
  --exclude-module scipy \
  main.py

echo "构建完成。输出目录：dist/"
