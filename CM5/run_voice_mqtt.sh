#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$BASE_DIR/.venv/bin:$PATH"
PY_BIN="$BASE_DIR/.venv/bin/python"
DAEMON_SCRIPT="$BASE_DIR/petbox_daemon.py"

# Load local secrets automatically
if [[ -f "$BASE_DIR/.env" ]]; then
  set -a
  source "$BASE_DIR/.env"
  set +a
fi

if [[ ! -x "$PY_BIN" ]]; then
  echo "error: python in venv not found: $PY_BIN"
  exit 1
fi

if [[ ! -f "$DAEMON_SCRIPT" ]]; then
  echo "error: daemon script not found: $DAEMON_SCRIPT"
  exit 1
fi

echo "🚀 Starting Petbox Consolidated Daemon..."
exec "$PY_BIN" "$DAEMON_SCRIPT"
