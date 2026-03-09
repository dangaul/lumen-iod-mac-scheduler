#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_PATH="$SCRIPT_DIR/config.json"
LOG_PATH="$SCRIPT_DIR/lumen-scheduler.log"

"$SCRIPT_DIR/stop_web_page.sh"
exec "$SCRIPT_DIR/launch_web_page.sh" "$CONFIG_PATH" "$LOG_PATH"
