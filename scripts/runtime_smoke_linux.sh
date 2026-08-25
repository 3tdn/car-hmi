#!/usr/bin/env bash
# Run runtime smoke tests (boots app via run_linux.sh and checks API/WS flows).
# Usage: ./scripts/runtime_smoke_linux.sh
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

export RUN_RUNTIME_SMOKE=1
pytest tests/2_functional_tests/runtime/test_runtime_smoke.py -q --tb=short
