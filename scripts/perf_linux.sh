#!/usr/bin/env bash
# Run k6 performance test script for car-hmi.
# Usage: ./scripts/perf_linux.sh [BASE_URL]
set -euo pipefail

BASE_URL="${1:-http://localhost:8000}"
REPORT_DIR="tests/3_performance/reports"
SCRIPT_PATH="tests/3_performance/scripts/load_test_homepage.js"

if ! command -v k6 >/dev/null 2>&1; then
  echo "k6 not found. Install k6 first: https://k6.io/docs/get-started/installation/" >&2
  exit 1
fi

mkdir -p "$REPORT_DIR"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUT_JSON="$REPORT_DIR/k6_${TIMESTAMP}.json"

echo "Running k6 against: $BASE_URL"
k6 run --out "json=$OUT_JSON" -e BASE_URL="$BASE_URL" "$SCRIPT_PATH"
echo "k6 report saved to: $OUT_JSON"
