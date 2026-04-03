#!/usr/bin/env bash
# Run/Install helper for Linux/macOS
# Usage: ./scripts/run_linux.sh [config/system.json] [INFO]
set -euo pipefail

CONFIG=${1:-config/system.json}
LOG_LEVEL=${2:-INFO}
INSTALL_ONLY=${3:-}

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found. Install Python >= 3.10 and try again." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv (.venv)"
  $PY -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

pip install --upgrade pip
pip install -e ".[dev]"

if [ -n "$INSTALL_ONLY" ]; then
  echo "Install-only requested, exiting"
  exit 0
fi

echo "Starting CAN-HMI (Ctrl+C to stop)"
python -m src.core.runner --config "$CONFIG" --log-level "$LOG_LEVEL"
