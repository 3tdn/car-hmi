#!/usr/bin/env bash
# Run helper for Linux/macOS (Bash)
#
# Usage:
#   bash scripts/run_linux.sh                     # start the application
#   bash scripts/run_linux.sh config/system.json  # custom config
#   bash scripts/run_linux.sh config/system.json INFO 9000  # custom port

set -euo pipefail

CONFIG="${1:-config/system.json}"
LOG_LEVEL="${2:-INFO}"
PORT="${3:-8000}"

log() { echo "[run] $*"; }

stop_process_on_port() {
    local port="$1"
    local pids
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    if [ -n "$pids" ]; then
        for pid in $pids; do
            local name
            name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            log "Stopping '$name' (PID $pid) on port $port"
            kill -9 "$pid" 2>/dev/null || true
        done
        sleep 0.8
        log "Port $port cleared."
    fi
}

VENV_PY=".venv/bin/python"

if [ ! -f "$VENV_PY" ]; then
    log "Virtualenv not found — running setup first..."
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SETUP_SCRIPT="$SCRIPT_DIR/setup_linux.sh"
    if [ -f "$SETUP_SCRIPT" ]; then
        bash "$SETUP_SCRIPT"
    else
        echo "setup_linux.sh not found at: $SETUP_SCRIPT" >&2
    fi
fi

if [ ! -f "$VENV_PY" ]; then
    log ".venv still missing — falling back to system Python."
    if command -v python3 &>/dev/null; then
        VENV_PY="python3"
    elif command -v python &>/dev/null; then
        VENV_PY="python"
    else
        echo "No Python interpreter found. Install Python >= 3.10 and retry." >&2
        exit 1
    fi
fi

stop_process_on_port "$PORT"

log "Starting CAN-HMI on port $PORT (press Ctrl+C to stop)"
"$VENV_PY" -m src.core.runner --config "$CONFIG" --log-level "$LOG_LEVEL"
