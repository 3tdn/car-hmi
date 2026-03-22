#!/usr/bin/env bash
# Run tests helper for Linux/macOS
# Usage: ./scripts/test_linux.sh
set -euo pipefail

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

pytest -q --tb=short --cov=src --cov-fail-under=60
