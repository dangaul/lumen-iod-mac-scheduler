#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AUTO_INSTALL_PYTHON="${AUTO_INSTALL_PYTHON:-0}"
AUTO_UPGRADE_PYTHON="${AUTO_UPGRADE_PYTHON:-1}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

prompt() {
  # Read a line from the terminal even when stdout is redirected.
  local reply
  read -r -p "$1" reply </dev/tty || reply=""
  echo "${reply}"
}

confirm() {
  # confirm "Question" [default: Y|n]
  local question="$1"
  local default="${2:-Y}"
  local reply
  reply="$(prompt "${question} [${default}]: ")"
  reply="${reply:-${default}}"
  [[ "${reply^^}" == "Y" ]]
}

# ---------------------------------------------------------------------------
# Platform check
# ---------------------------------------------------------------------------

echo ""
echo "=================================================="
echo " Lumen Bandwidth Scheduler — macOS Installer"
echo "=================================================="
echo ""

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "[install] ERROR: This script is intended for macOS."
  exit 1
fi

# ---------------------------------------------------------------------------
# Python detection
# ---------------------------------------------------------------------------

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
  local cand ver major minor
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
    major="${ver%%.*}"; minor="${ver##*.}"
    if [[ "${major}" -gt 3 ]] || [[ "${major}" -eq 3 && "${minor}" -ge 10 ]]; then
      echo "${cand}"; return 0
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python_310_plus || true)"

if [[ -z "${PYTHON_BIN}" ]]; then
  if command -v brew >/dev/null 2>&1 && [[ "${AUTO_UPGRADE_PYTHON}" == "1" ]]; then
    echo "[install] Python 3.10+ not found. Installing via Homebrew..."
    brew install python
    PYTHON_BIN="$(find_python_310_plus || true)"
  fi
fi

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "[install] ERROR: Python 3.10+ is required."
  echo "[install] Rerun with: AUTO_UPGRADE_PYTHON=1 ./install_macos.sh"
  exit 1
fi

PYVER="$("${PYTHON_BIN}" -c 'import sys;print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
echo "[install] Python ${PYVER} -> ${PYTHON_BIN}"

if command -v crontab >/dev/null 2>&1; then
  echo "[install] crontab found."
else
  echo "[install] WARNING: crontab not found. Cron features will be unavailable."
fi

cd "${ROOT_DIR}"

# ---------------------------------------------------------------------------
# Detect fresh install vs update
# ---------------------------------------------------------------------------

IS_UPDATE=false
[[ -f "./config.json" ]] && IS_UPDATE=true

if $IS_UPDATE; then
  echo ""
  echo "[install] Existing installation detected in: ${ROOT_DIR}"
  echo ""
  echo "  What would you like to do?"
  echo "    1) Update scripts, keep config.json and .env  (recommended)"
  echo "    2) Full reinstall — reset config.json and .env from templates"
  echo "    3) Cancel"
  echo ""
  INSTALL_CHOICE="$(prompt "  Choice [1]: ")"
  INSTALL_CHOICE="${INSTALL_CHOICE:-1}"

  case "${INSTALL_CHOICE}" in
    1) echo "" && echo "[install] Mode: update existing install." ;;
    2)
      echo ""
      echo "[install] Mode: full reinstall (config.json and .env will be reset)."
      # Offer to back up existing files before wiping them.
      BACKUP_STAMP="$(date +%Y%m%d-%H%M%S)"
      BACKED_UP=()
      if confirm "  Back up existing config.json and .env before resetting?" "Y"; then
        for f in "./config.json" "./.env"; do
          if [[ -f "${f}" ]]; then
            cp "${f}" "${f}.backup-${BACKUP_STAMP}"
            BACKED_UP+=("${f}.backup-${BACKUP_STAMP}")
          fi
        done
        if [[ ${#BACKED_UP[@]} -gt 0 ]]; then
          echo "[install] Backed up:"
          for b in "${BACKED_UP[@]}"; do echo "[install]   ${b}"; done
        fi
      else
        echo "[install] Skipping backup — existing files will be overwritten."
      fi
      IS_UPDATE=false
      ;;
    *) echo "[install] Cancelled." && exit 0 ;;
  esac
else
  echo "[install] Mode: fresh install."
fi

# ---------------------------------------------------------------------------
# Dashboard — stop before updating if running
# ---------------------------------------------------------------------------

DASHBOARD_WAS_RUNNING=false
if $IS_UPDATE && pgrep -f "dashboard.py" >/dev/null 2>&1; then
  echo ""
  echo "[install] Dashboard is currently running."
  if confirm "  Stop it now and restart after the update?" "Y"; then
    pkill -f "dashboard.py" 2>/dev/null || true
    sleep 1
    echo "[install] Dashboard stopped."
    DASHBOARD_WAS_RUNNING=true
  else
    echo "[install] Dashboard left running. Some files may not reload until it is restarted."
  fi
fi

# ---------------------------------------------------------------------------
# config.json
# ---------------------------------------------------------------------------

echo ""
if ! $IS_UPDATE || [[ ! -f "./config.json" ]]; then
  cp "./config.example.json" "./config.json"
  echo "[install] Created ./config.json from template."
else
  echo "[install] config.json — keeping existing file."

  # Warn about new sections added since the last release.
  MISSING_KEYS=()
  if ! "${PYTHON_BIN}" -c "import json,sys; d=json.load(open('config.json')); sys.exit(0 if 'notifications' in d else 1)" 2>/dev/null; then
    MISSING_KEYS+=("notifications")
  fi

  if [[ ${#MISSING_KEYS[@]} -gt 0 ]]; then
    echo ""
    echo "[install] ⚠  config.json is missing new section(s): ${MISSING_KEYS[*]}"
    echo "[install]    Add the following to config.json (see config.example.json for full structure):"
    echo ""
    if [[ " ${MISSING_KEYS[*]} " == *" notifications "* ]]; then
      cat <<'SNIPPET'
  "notifications": {
    "teams_webhook_url": "${TEAMS_WEBHOOK_URL:-}",
    "on_apply_failure": true,
    "on_pending_timeout": true,
    "on_recovery": false
  },
SNIPPET
    fi
  else
    echo "[install] config.json sections — up to date."
  fi
fi

# ---------------------------------------------------------------------------
# .env
# ---------------------------------------------------------------------------

if ! $IS_UPDATE || [[ ! -f "./.env" ]]; then
  cp "./.env.example" "./.env"
  chmod 600 "./.env" || true
  echo "[install] Created ./.env from template."
  echo "[install] Fill in your Lumen credentials before running."
else
  chmod 600 "./.env" || true
  echo "[install] .env — keeping existing file."

  # Warn about new variables added since the last release.
  MISSING_VARS=()
  if ! grep -q "^TEAMS_WEBHOOK_URL=" "./.env" 2>/dev/null; then
    MISSING_VARS+=("TEAMS_WEBHOOK_URL")
  fi

  if [[ ${#MISSING_VARS[@]} -gt 0 ]]; then
    echo ""
    echo "[install] ⚠  .env is missing new variable(s): ${MISSING_VARS[*]}"
    echo "[install]    Add the following line(s) to .env:"
    for v in "${MISSING_VARS[@]}"; do
      echo "             ${v}="
    done
  else
    echo "[install] .env variables — up to date."
  fi
fi

# ---------------------------------------------------------------------------
# Placeholder service_id check
# ---------------------------------------------------------------------------

SERVICE_ID="$("${PYTHON_BIN}" -c "import json; d=json.load(open('config.json')); print(d.get('lumen_iod',{}).get('service_id',''))" 2>/dev/null || true)"
if [[ -z "${SERVICE_ID}" || "${SERVICE_ID}" == YOUR_* || "${SERVICE_ID}" == "77123456789" ]]; then
  echo ""
  echo "[install] ⚠  lumen_iod.service_id in config.json is still a placeholder."

  CREDS_PRESENT="$("${PYTHON_BIN}" -c "
import os, re
env_text = open('.env').read() if os.path.exists('.env') else ''
has_creds = bool(re.search(r'^(LUMEN_BASIC_SECRET|LUMEN_CLIENT_SECRET|LUMEN_API_KEY)=.+', env_text, re.M))
has_customer = bool(re.search(r'^LUMEN_CUSTOMER_NUMBER=.+', env_text, re.M))
print('yes' if has_creds and has_customer else 'no')
" 2>/dev/null || echo "no")"

  if [[ "${CREDS_PRESENT}" == "yes" ]]; then
    echo "[install]    Credentials found — querying Lumen API for available service IDs..."
    echo ""
    "${PYTHON_BIN}" ./lumen_scheduler.py --config ./config.json list-services 2>&1 || true
  else
    echo "[install]    After filling in .env, run:"
    echo "[install]      python3 ./lumen_scheduler.py --config ./config.json list-services"
  fi
fi

# ---------------------------------------------------------------------------
# Permissions, syntax check, tests
# ---------------------------------------------------------------------------

echo ""
chmod +x "./lumen_scheduler.py" "./dashboard.py" "./launch_web_page.sh" "./stop_web_page.sh" "./restart_web_page.sh" || true

echo "[install] Running syntax checks..."
"${PYTHON_BIN}" -m py_compile "./lumen_scheduler.py" "./dashboard.py"
echo "[install] Syntax OK."

echo "[install] Running unit tests..."
if "${PYTHON_BIN}" -m unittest test_lumen_scheduler.py 2>&1; then
  echo "[install] All tests passed."
else
  echo "[install] WARNING: Some tests failed. Review output before running in production."
fi

# ---------------------------------------------------------------------------
# Restart dashboard if it was stopped
# ---------------------------------------------------------------------------

if $DASHBOARD_WAS_RUNNING; then
  echo ""
  if confirm "  Restart dashboard now?" "Y"; then
    nohup "${SHELL}" ./launch_web_page.sh >/dev/null 2>&1 &
    sleep 2
    if pgrep -f "dashboard.py" >/dev/null 2>&1; then
      echo "[install] Dashboard restarted."
    else
      echo "[install] WARNING: Dashboard did not start. Run ./launch_web_page.sh manually."
    fi
  else
    echo "[install] Run ./launch_web_page.sh when ready."
  fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "=================================================="
if $IS_UPDATE; then
  echo " Update complete."
else
  echo " Installation complete."
fi
echo "=================================================="
echo ""

if ! $IS_UPDATE; then
  cat <<'EOF'
Next steps:
  1) Edit .env with your Lumen API credentials and Teams webhook URL.
  2) Edit config.json — set lumen_iod.service_id to your real service ID.
  3) Validate:
       python3 ./lumen_scheduler.py --config ./config.json status
  4) Launch dashboard:
       ./launch_web_page.sh
EOF
else
  echo "  Run ./launch_web_page.sh if the dashboard is not already running."
  echo "  Apply any config.json / .env changes flagged above."
fi
echo ""
