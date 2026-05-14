#!/usr/bin/env bash
# create_portable_zip.sh — build a self-extracting installer for the Lumen Bandwidth Scheduler.
#
# Output (in ./dist/):
#   lumen-scheduler-macos-<timestamp>.sh   — self-extracting installer (transfer this one file)
#   lumen-scheduler-macos-<timestamp>.zip  — plain zip (attach to GitHub Releases)
#
# How the self-extractor works:
#   The .sh file is a bash script with the zip binary appended after an "exit 0".
#   PAYLOAD_OFFSET records the exact byte position where the zip starts.
#   When run, dd skips that many bytes to extract the zip to a temp file, then
#   unzip unpacks it to the target directory and install_macos.sh is invoked.
#
#   Offset calculation is done in two passes because substituting the placeholder
#   value (__PAYLOAD_OFFSET__) may change the file size by a few bytes if the
#   digit count of the real offset differs from the placeholder length.
#
# Usage:
#   bash create_portable_zip.sh
#   bash test_installer.sh          # verify the output
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${ROOT_DIR}/dist"
STAMP="$(date +%Y%m%d-%H%M%S)"
ZIP_FILE="${OUT_DIR}/lumen-scheduler-macos-${STAMP}.zip"
SH_FILE="${OUT_DIR}/lumen-scheduler-macos-${STAMP}.sh"

mkdir -p "${OUT_DIR}"

cd "${ROOT_DIR}"

# ---------------------------------------------------------------------------
# Step 1: Build the zip — templates and scripts only, never secrets or state.
# ---------------------------------------------------------------------------

zip -r "${ZIP_FILE}" \
  "README.md" \
  "INSTALL.md" \
  "lumen_scheduler.py" \
  "dashboard.py" \
  "test_lumen_scheduler.py" \
  "test_installer.sh" \
  "config.example.json" \
  ".env.example" \
  "create_portable_zip.sh" \
  "launch_web_page.sh" \
  "stop_web_page.sh" \
  "restart_web_page.sh" \
  "install_macos.sh" \
  -x "*/__pycache__/*" "*.pyc" "*.DS_Store" >/dev/null

# ---------------------------------------------------------------------------
# Step 2: Write the self-extractor header to a temp file.
# __PAYLOAD_OFFSET__ is a placeholder; the real value is substituted below.
# ---------------------------------------------------------------------------

HEADER_TMP="$(mktemp /tmp/lumen-header-XXXXXX.sh)"
trap 'rm -f "${HEADER_TMP}"' EXIT

cat > "${HEADER_TMP}" <<'HEADER_SENTINEL'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
TARGET_DIR="${1:-${HOME}/lumen-scheduler}"

echo ""
echo "=================================================="
echo " Lumen Bandwidth Scheduler — Installer"
echo "=================================================="
echo ""
echo "[deploy] Target: ${TARGET_DIR}"

mkdir -p "${TARGET_DIR}"

# PAYLOAD_OFFSET is the byte count of this script header, set at build time.
# dd skips exactly that many bytes from the .sh file to isolate the zip payload.
PAYLOAD_OFFSET=__PAYLOAD_OFFSET__
TMP_ZIP="$(mktemp /tmp/lumen-installer-XXXXXX.zip)"
trap 'rm -f "${TMP_ZIP}"' EXIT
dd bs=1 skip="${PAYLOAD_OFFSET}" if="${SCRIPT}" of="${TMP_ZIP}" 2>/dev/null
echo "[deploy] Extracting files..."
unzip -o "${TMP_ZIP}" -d "${TARGET_DIR}" >/dev/null
cd "${TARGET_DIR}"
echo "[deploy] Running installer..."
echo ""
bash install_macos.sh
exit 0
HEADER_SENTINEL

# ---------------------------------------------------------------------------
# Step 3: Two-pass offset substitution.
# Pass 1 — substitute the placeholder with the initial byte count.
# Pass 2 — recalculate after substitution (digit count may have changed) and
#           update in place so dd gets the exact final offset.
# ---------------------------------------------------------------------------

OFFSET=$(wc -c < "${HEADER_TMP}" | tr -d ' ')
sed "s/__PAYLOAD_OFFSET__/${OFFSET}/" "${HEADER_TMP}" > "${SH_FILE}"

OFFSET2=$(wc -c < "${SH_FILE}" | tr -d ' ')
sed -i '' "s/PAYLOAD_OFFSET=${OFFSET}/PAYLOAD_OFFSET=${OFFSET2}/" "${SH_FILE}"

# ---------------------------------------------------------------------------
# Step 4: Append the zip and mark the file executable.
# ---------------------------------------------------------------------------

cat "${ZIP_FILE}" >> "${SH_FILE}"
chmod +x "${SH_FILE}"

echo "[package] Zip:       ${ZIP_FILE}"
echo "[package] Installer: ${SH_FILE}"
echo "[package] Usage: bash lumen-scheduler-macos-*.sh [target_dir]"
echo "[package] Default target: ~/lumen-scheduler"
echo "[package] Contains templates only (no .env, no config.json, no logs/state)."
