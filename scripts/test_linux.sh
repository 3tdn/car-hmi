#!/usr/bin/env bash
# Run tests helper for Linux/macOS
# Usage: ./scripts/test_linux.sh [all|unit|functional|api|ws|integration|security]
set -euo pipefail

SUITE="${1:-all}"

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

case "$SUITE" in
  all) TARGET="tests" ;;
  unit) TARGET="tests/1_unit_functions" ;;
  functional) TARGET="tests/2_functional_tests" ;;
  api) TARGET="tests/2_functional_tests/api" ;;
  ws) TARGET="tests/2_functional_tests/websockets" ;;
  integration) TARGET="tests/2_functional_tests/integration" ;;
  security) TARGET="tests/4_security" ;;
  *)
    echo "Unknown suite: $SUITE" >&2
    echo "Usage: ./scripts/test_linux.sh [all|unit|functional|api|ws|integration|security]" >&2
    exit 1
    ;;
esac

echo "Running pytest suite: $SUITE ($TARGET)"

# Run pytest with coverage and produce HTML reports
pytest "$TARGET" -q --tb=short --cov=src --cov-fail-under=60 \
  --cov-report=html:reports/coverage_html \
  --cov-report=term-missing \
  --html=reports/report.html --self-contained-html
