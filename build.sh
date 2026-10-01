#!/usr/bin/env bash
# Render build command: bash build.sh
set -euo pipefail

pip install -r requirements.txt

# mediapipe depends on opencv-contrib-python, the desktop OpenCV build, which
# needs libGL — not present on Render — so `import cv2` would crash the app at
# startup. Swap it for the headless build (same cv2 API, no GUI libraries).
pip uninstall -y opencv-contrib-python
pip install --force-reinstall --no-deps "opencv-python-headless>=4.8.0,<5"
