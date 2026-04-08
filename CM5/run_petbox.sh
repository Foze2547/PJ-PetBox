#!/usr/bin/env bash
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_BIN="$BASE_DIR/.venv/bin/python"

# Load environment variables if they exist
if [ -f "$BASE_DIR/.env" ]; then
    export $(grep -v '^#' "$BASE_DIR/.env" | xargs)
fi

echo "🚀 Starting Petbox Unified Daemon..."
echo "Config: PC_HOST=${PC_HOST:-100.110.201.13}"

# Execute the daemon
cd "$BASE_DIR"
"$VENV_BIN" petbox_daemon.py
