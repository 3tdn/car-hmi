#!/usr/bin/env bash
# Run backend + npm frontend from any working directory.
# Usage: bash scripts/run_linux.sh [config/system.json] [INFO] [8000]
# Optional --backend-only runs just the supervised Python backend.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/linux_common.sh"
BACKEND_ONLY=false
if [[ "${1:-}" == --backend-only ]]; then
    BACKEND_ONLY=true
    shift
fi
CONFIG="${1:-config/system.json}"
LOG_LEVEL="${2:-INFO}"
export PORT="${3:-${PORT:-8000}}"
export CAR_HMI_CONFIG="$CONFIG"
VENV_PY="$PROJECT_DIR/.venv/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    bash "$PROJECT_DIR/scripts/setup_linux.sh"
fi
if [[ "$BACKEND_ONLY" == true ]]; then
    exec "$VENV_PY" -m src.core.service --config "$CONFIG" --log-level "$LOG_LEVEL"
fi
require_frontend_runtime
if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
    npm --prefix "$FRONTEND_DIR" ci --omit=dev
fi
backend_pid=''
frontend_pid=''
cleanup() {
    trap '' INT TERM
    for pid in "$backend_pid" "$frontend_pid"; do
        [[ -z "$pid" ]] || kill -TERM "$pid" 2>/dev/null || true
    done
    for pid in "$backend_pid" "$frontend_pid"; do
        [[ -z "$pid" ]] || wait "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
"$VENV_PY" -m src.core.service --config "$CONFIG" --log-level "$LOG_LEVEL" &
backend_pid=$!
# Direct node launch gives the shell ownership of the actual process (npm start uses the same entry).
node "$FRONTEND_DIR/server.mjs" &
frontend_pid=$!
echo "[run] Backend: http://127.0.0.1:$PORT; frontend: http://localhost:${CAR_HMI_FRONTEND_PORT:-5173}"
# If either service exits, clean up its sibling and preserve the failure status.
result=0
wait -n "$backend_pid" "$frontend_pid" || result=$?
exit "$result"
