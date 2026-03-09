#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_INSTALL_PYTHON="${AUTO_INSTALL_PYTHON:-0}"
AUTO_UPGRADE_PYTHON="${AUTO_UPGRADE_PYTHON:-1}"

echo "[install] Lumen Scheduler macOS bootstrap"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[install] This script is intended for macOS."
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "[install] python3 not found."
  if [[ "${AUTO_INSTALL_PYTHON}" == "1" ]] && command -v brew >/dev/null 2>&1; then
    echo "[install] Installing python via Homebrew..."
    brew install python
  else
    echo "[install] Install Python 3.10+ and rerun."
    echo "[install] Tip: brew install python"
    exit 1
  fi
fi

find_python_310_plus() {
  local cand
  local ver
  local major
  local minor
  for cand in \
    "$(command -v python3 2>/dev/null || true)" \
    /opt/homebrew/bin/python3 \
    /usr/local/bin/python3 \
    /opt/homebrew/bin/python3.13 \
    /opt/homebrew/bin/python3.12 \
    /opt/homebrew/bin/python3.11 \
    /opt/homebrew/bin/python3.10 \
    /usr/local/bin/python3.13 \
    /usr/local/bin/python3.12 \
    /usr/local/bin/python3.11 \
    /usr/local/bin/python3.10; do
    [[ -n "${cand}" ]] || continue
    [[ -x "${cand}" ]] || continue
    ver="$("${cand}" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
    [[ -n "${ver}" ]] || continue
    major="${ver%%.*}"
    minor="${ver##*.}"
    if [[ "${major}" -gt 3 ]] || [[ "${major}" -eq 3 && "${minor}" -ge 10 ]]; then
      echo "${cand}"
      return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python_310_plus || true)"

if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v brew >/dev/null 2>&1 && [[ "${AUTO_UPGRADE_PYTHON}" == "1" ]]; then
    echo "[install] Python 3.10+ not found. Installing/upgrading python via Homebrew..."
    brew install python
    PYTHON_BIN="$(find_python_310_plus || true)"
  fi
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[install] Python 3.10+ is required."
  echo "[install] If Homebrew is available, rerun with: AUTO_UPGRADE_PYTHON=1 ./install_macos.sh"
  exit 1
fi

PYVER="$("${PYTHON_BIN}" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[install] python ok (${PYVER}) -> ${PYTHON_BIN}"

if command -v crontab >/dev/null 2>&1; then
  echo "[install] crontab found."
else
  echo "[install] WARNING: crontab not found. Scheduler cron features will be unavailable."
fi

cd "${ROOT_DIR}"

if [[ ! -f "./config.json" ]]; then
  cp "./config.example.json" "./config.json"
  echo "[install] Created ./config.json from template."
else
  echo "[install] config.json already exists (left unchanged)."
fi

if [[ ! -f "./.env" ]]; then
  cp "./.env.example" "./.env"
  chmod 600 "./.env" || true
  echo "[install] Created ./.env from template."
  echo "[install] Fill in Lumen credentials in ./.env before running."
else
  chmod 600 "./.env" || true
  echo "[install] .env already exists (left unchanged)."
fi

chmod +x "./lumen_scheduler.py" "./dashboard.py" "./launch_web_page.sh" "./stop_web_page.sh" "./restart_web_page.sh" || true

echo "[install] Running syntax checks..."
"${PYTHON_BIN}" -m py_compile "./lumen_scheduler.py" "./dashboard.py"

cat <<'EOF'
[install] Done.

Next steps:
1) Edit ./.env with your API credentials.
2) Review ./config.json (service_id, timezone, rules, bandwidth profiles).
3) Validate:
   python3 ./lumen_scheduler.py --config ./config.json status
4) Launch dashboard:
   ./launch_web_page.sh
EOF
