#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --upgrade pip
python -m pip install -r requirements-full.txt
python -m pip install pyinstaller

MODE="--onefile"
if [[ "${1:-}" == "--onedir" ]]; then
  MODE=""
fi

python -m PyInstaller \
  ${MODE} \
  --noconfirm --clean --windowed \
  --name yolo-viewer-full \
  main.py

echo "构建完成。输出目录：dist/"
