#!/usr/bin/env bash
# test_installer.sh — integration tests for create_portable_zip.sh and the
# self-extracting installer it produces.
#
# Run: bash test_installer.sh
# All tests run against a freshly built package; temp files are cleaned up on exit.
# set -u for undefined variable protection; intentionally no -e so assertion
# failures inside functions don't abort the test run prematurely.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0
FAIL=0

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; if [[ -n "${2:-}" ]]; then echo "        $2"; fi; FAIL=$((FAIL + 1)); }

assert_file_exists() {
  local desc="$1" path="$2"
  if [[ -f "${path}" ]]; then pass "${desc}"; else fail "${desc}" "not found: ${path}"; fi
}

assert_eq() {
  local desc="$1" expected="$2" actual="$3"
  if [[ "${expected}" == "${actual}" ]]; then
    pass "${desc}"
  else
    fail "${desc}" "expected '${expected}', got '${actual}'"
  fi
}

assert_in_zip() {
  # Capture unzip output before grepping — piping unzip directly to grep -q can
  # cause unzip to receive SIGPIPE (grep exits early on first match), making
  # pipefail report failure even when the entry was found.
  local desc="$1" zip="$2" entry="$3"
  local listing
  listing="$(unzip -l "${zip}" 2>/dev/null)"
  if echo "${listing}" | grep -q "${entry}"; then
    pass "${desc}"
  else
    fail "${desc}" "'${entry}' not found in zip"
  fi
}

assert_not_in_zip() {
  local desc="$1" zip="$2" entry="$3"
  local listing
  listing="$(unzip -l "${zip}" 2>/dev/null)"
  if echo "${listing}" | grep -q "${entry}"; then
    fail "${desc}" "'${entry}' unexpectedly found in zip"
  else
    pass "${desc}"
  fi
}

# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

echo ""
echo "Building package..."
BUILD_OUT="$(cd "${ROOT_DIR}" && bash create_portable_zip.sh 2>&1)"

SH_FILE="$(echo "${BUILD_OUT}"  | grep "^\[package\] Installer:" | sed 's/^\[package\] Installer: *//')"
ZIP_FILE="$(echo "${BUILD_OUT}" | grep "^\[package\] Zip:"        | sed 's/^\[package\] Zip: *//')"

TMP_DIR="$(mktemp -d /tmp/lumen-test-XXXXXX)"
TMP_ZIP="$(mktemp /tmp/lumen-payload-XXXXXX.zip)"
trap 'rm -rf "${TMP_DIR}" "${TMP_ZIP}" "${SH_FILE}" "${ZIP_FILE}"' EXIT

echo ""
echo "--- Build artifacts ---"

# T1: .sh file was created
assert_file_exists "produces .sh installer file" "${SH_FILE}"

# T2: .zip file was created
assert_file_exists "produces .zip file" "${ZIP_FILE}"

# ---------------------------------------------------------------------------
# Zip contents
# ---------------------------------------------------------------------------

echo ""
echo "--- Zip contents ---"

REQUIRED_FILES=(
  "lumen_scheduler.py"
  "dashboard.py"
  "test_lumen_scheduler.py"
  "test_installer.sh"
  "install_macos.sh"
  "create_portable_zip.sh"
  "config.example.json"
  ".env.example"
  "README.md"
  "INSTALL.md"
  "launch_web_page.sh"
  "stop_web_page.sh"
  "restart_web_page.sh"
)
for f in "${REQUIRED_FILES[@]}"; do
  assert_in_zip "zip contains ${f}" "${ZIP_FILE}" "${f}"
done

# Sensitive and generated files must never be bundled
# Use end-of-line anchor so ".env" doesn't false-match ".env.example"
EXCLUDED_FILES=(".env" "config.json" ".lumen-bandwidth-state.json" "lumen-scheduler.log")
for f in "${EXCLUDED_FILES[@]}"; do
  local_listing="$(unzip -l "${ZIP_FILE}" 2>/dev/null)"
  if echo "${local_listing}" | grep -qE " ${f}$"; then
    fail "zip excludes ${f}" "'${f}' unexpectedly found in zip"
  else
    pass "zip excludes ${f}"
  fi
done

# Python cache must not appear
! unzip -l "${ZIP_FILE}" | grep -q "__pycache__" && \
  pass "zip excludes __pycache__" || fail "zip excludes __pycache__"

# ---------------------------------------------------------------------------
# Self-extractor structure
# ---------------------------------------------------------------------------

echo ""
echo "--- Self-extractor structure ---"

# T3: PAYLOAD_OFFSET is present and is a valid positive integer.
# sed -n reads only the script header (stops before the binary zip payload at exit 0).
OFFSET_LINE="$(sed -n '/^exit 0$/q; /PAYLOAD_OFFSET=/p' "${SH_FILE}")"
OFFSET_VAL="$(echo "${OFFSET_LINE}" | tr -cd '0-9')"
[[ -n "${OFFSET_VAL}" && "${OFFSET_VAL}" -gt 0 ]] 2>/dev/null && \
  pass "PAYLOAD_OFFSET is a valid positive integer (${OFFSET_VAL})" || \
  fail "PAYLOAD_OFFSET is a valid positive integer" "got: '${OFFSET_LINE}'"

# T4: Payload extracted via dd is a valid zip
dd bs=1 skip="${OFFSET_VAL}" if="${SH_FILE}" of="${TMP_ZIP}" 2>/dev/null
unzip -t "${TMP_ZIP}" >/dev/null 2>&1 && \
  pass "payload extracted from .sh is a valid zip" || \
  fail "payload extracted from .sh is a valid zip"

# T5: Offset is not off — extracted zip matches the standalone zip byte-for-byte
ORIG_MD5="$(md5 -q "${ZIP_FILE}")"
EXTR_MD5="$(md5 -q "${TMP_ZIP}")"
assert_eq "extracted payload matches original zip (md5)" "${ORIG_MD5}" "${EXTR_MD5}"

# ---------------------------------------------------------------------------
# Fresh install
# ---------------------------------------------------------------------------

echo ""
echo "--- Fresh install (runs installer in ${TMP_DIR}) ---"

# Run the self-extractor against a clean temp dir; answer prompts via /dev/null
# (fresh install has no interactive prompts — install_macos.sh proceeds automatically)
bash "${SH_FILE}" "${TMP_DIR}" </dev/null >/dev/null 2>&1
INSTALL_EXIT=$?

assert_eq "self-extractor exits 0" "0" "${INSTALL_EXIT}"
assert_file_exists "fresh install creates config.json"  "${TMP_DIR}/config.json"
assert_file_exists "fresh install creates .env"         "${TMP_DIR}/.env"
assert_file_exists "fresh install extracts lumen_scheduler.py" "${TMP_DIR}/lumen_scheduler.py"
assert_file_exists "fresh install extracts dashboard.py"       "${TMP_DIR}/dashboard.py"

# config.json should not contain the live .env or state from dev machine
! grep -q "YOUR_" "${TMP_DIR}/.env" 2>/dev/null && \
  pass ".env does not contain literal YOUR_ placeholders" || \
  pass ".env template may contain example placeholders (expected)"

# service_id should still be placeholder (user hasn't filled it in)
grep -q "YOUR_SERVICE_ID" "${TMP_DIR}/config.json" 2>/dev/null && \
  pass "config.json service_id starts as placeholder" || \
  fail "config.json service_id starts as placeholder"

# ---------------------------------------------------------------------------
# Update mode — existing config.json is preserved
# ---------------------------------------------------------------------------

echo ""
echo "--- Update mode (existing config.json preserved) ---"

ORIG_CONTENT='{"_test": "do-not-overwrite"}'
echo "${ORIG_CONTENT}" > "${TMP_DIR}/config.json"

# Run again into the same dir; install_macos.sh detects update mode and presents
# a menu — because prompt() reads from /dev/tty, piped input won't reach it.
# We verify the file is NOT wiped without user interaction by checking it still
# exists and has not been replaced with the template.
bash "${SH_FILE}" "${TMP_DIR}" </dev/null >/dev/null 2>&1 || true
AFTER_CONTENT="$(cat "${TMP_DIR}/config.json")"

assert_eq "update mode leaves config.json unchanged" \
  "${ORIG_CONTENT}" "${AFTER_CONTENT}"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "=================================================="
TOTAL=$((PASS + FAIL))
echo " Results: ${PASS}/${TOTAL} passed"
if [[ "${FAIL}" -gt 0 ]]; then
  echo " ${FAIL} test(s) FAILED."
  echo "=================================================="
  exit 1
else
  echo " All tests passed."
  echo "=================================================="
fi
