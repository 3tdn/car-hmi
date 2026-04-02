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

# Ensure reports directory exists
mkdir -p reports

# Ensure pytest-html is installed in the venv
if ! pip show pytest-html >/dev/null 2>&1; then
  echo "Installing pytest-html into virtualenv"
  pip install pytest-html
fi

# Ensure pytest-cov is installed in the venv
if ! pip show pytest-cov >/dev/null 2>&1; then
  echo "Installing pytest-cov into virtualenv"
  pip install pytest-cov
fi

# Run pytest with coverage and produce HTML reports
pytest -q --tb=short --cov=src --cov-fail-under=60 \
  --cov-report=html:reports/coverage_html \
  --cov-report=term-missing \
  --html=reports/report.html --self-contained-html
