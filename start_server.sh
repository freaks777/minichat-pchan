#!/usr/bin/env bash
# RP Standalone Server Launcher
# Usage: ./start_server.sh [--debug] [--model MODEL_ID]
set -euo pipefail

WORKDIR="$(dirname "$0")/backend"

echo "=== RP Standalone Server ==="
echo "Port: 8765"
echo

cd "$WORKDIR"
exec python main.py "$@"
