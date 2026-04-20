#!/usr/bin/env bash
# Integration test runner for Linux/macOS
# Usage: ./scripts/test_it_linux.sh [--verbose] [--install]
#
# This script will:
#  - ensure .venv exists (creates if missing)
#  - optionally install dependencies if --install passed
#  - run ONLY tests/test_integration.py with coverage
#  - generate HTML test report  → reports/it_report.html
#  - generate HTML coverage      → reports/it_coverage_html/
set -euo pipefail

VERBOSE=0
INSTALL=0

for arg in "$@"; do
  case "$arg" in
    --verbose|-v) VERBOSE=1 ;;
    --install)    INSTALL=1 ;;
  esac
done

PY=python3
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 not found. Install Python >= 3.10 and try again." >&2
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "[it-test] Creating virtualenv (.venv)"
  $PY -m venv .venv
fi

# shellcheck source=/dev/null
source .venv/bin/activate

if [ "$INSTALL" -eq 1 ]; then
  echo "[it-test] Installing dependencies (editable + dev)"
  pip install --upgrade pip
  pip install -e ".[dev]"
fi

# Ensure reports directory exists
mkdir -p reports

# Ensure pytest-html is installed
if ! pip show pytest-html >/dev/null 2>&1; then
  echo "[it-test] Installing pytest-html"
  pip install pytest-html
fi

# Ensure pytest-cov is installed
if ! pip show pytest-cov >/dev/null 2>&1; then
  echo "[it-test] Installing pytest-cov"
  pip install pytest-cov
fi

VERB_FLAG="-q"
if [ "$VERBOSE" -eq 1 ]; then
  VERB_FLAG="-v"
fi

echo "[it-test] Running integration tests (tests/test_integration.py)"
pytest tests/test_integration.py "$VERB_FLAG" --tb=short \
  --cov=src \
  --cov-fail-under=0 \
  --cov-report=html:reports/it_coverage_html \
  --cov-report=term-missing \
  --html=reports/it_report.html --self-contained-html

echo "[it-test] Integration tests PASSED"
echo "[it-test] Test report : reports/it_report.html"
echo "[it-test] Coverage    : reports/it_coverage_html/index.html"
