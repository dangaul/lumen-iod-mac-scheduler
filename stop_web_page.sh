#!/bin/zsh
set -euo pipefail

if pkill -f "dashboard.py" >/dev/null 2>&1; then
  echo "Stopped dashboard process(es)."
else
  echo "No dashboard process found."
fi
