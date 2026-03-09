#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${ROOT_DIR}/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT_FILE="${OUT_DIR}/lumen-scheduler-macos-${STAMP}.zip"

mkdir -p "${OUT_DIR}"

cd "${ROOT_DIR}"

zip -r "${OUT_FILE}" \
  "README.md" \
  "lumen_scheduler.py" \
  "dashboard.py" \
  "config.example.json" \
  ".env.example" \
  "launch_web_page.sh" \
  "stop_web_page.sh" \
  "restart_web_page.sh" \
  "install_macos.sh" \
  -x "*/__pycache__/*" "*.pyc" "*.DS_Store" >/dev/null

echo "[package] Created: ${OUT_FILE}"
echo "[package] Contains templates only (no .env, no config.json, no logs/state)."

