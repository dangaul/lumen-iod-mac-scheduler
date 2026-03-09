#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_PATH="${1:-$SCRIPT_DIR/config.json}"
LOG_PATH="${2:-$SCRIPT_DIR/lumen-scheduler.log}"
PORT="${PORT:-8787}"

is_port_in_use() {
  local port="$1"
  lsof -iTCP:"$port" -sTCP:LISTEN -n -P >/dev/null 2>&1
}

if is_port_in_use "$PORT"; then
  echo "Port $PORT is already in use. Searching for a free port..."
  for p in {8788..8810}; do
    if ! is_port_in_use "$p"; then
      PORT="$p"
      break
    fi
  done
fi

python3 "$SCRIPT_DIR/dashboard.py" \
  --config "$CONFIG_PATH" \
  --log-file "$LOG_PATH" \
  --port "$PORT" &
DASH_PID=$!

sleep 1
if ! kill -0 "$DASH_PID" >/dev/null 2>&1; then
  echo "Dashboard failed to start. Check logs/output."
  exit 1
fi
if command -v open >/dev/null 2>&1; then
  open "http://127.0.0.1:${PORT}" || true
fi

echo "Dashboard PID: $DASH_PID"
echo "URL: http://127.0.0.1:${PORT}"
echo "Press Ctrl+C to stop."

wait "$DASH_PID"
