#!/usr/bin/env bash
################################################################################
# CAN-HMI Run Script — Linux/macOS
################################################################################
# Purpose: Start the CAN-HMI application (FastAPI server + signal pipeline)
# Requirement: Python >= 3.10 must be installed (via pyenv or system Python)
#
# Usage:
#   bash scripts/run_linux.sh                     # Use the default configuration (config/system.json, port 8000)
#   bash scripts/run_linux.sh config/system.json  # Specify a custom configuration file
#   bash scripts/run_linux.sh config/system.json INFO 9000  # Custom config + log level + port
#
# Parameters:
#   \$1 CONFIG    — Configuration file path (default: config/system.json)
#   \$2 LOG_LEVEL — Logging level: DEBUG|INFO|WARNING|ERROR (default: INFO)
#   \$3 PORT      — API server port (default: 8000)
#
# Execution flow:
#   1. Validate and assign argument values
#   2. Initialize pyenv (if available)
#   3. Stop any process already using the port (avoid port conflicts)
#   4. Check the venv — run setup_linux.sh if it is missing
#   5. Run the application via python -m src.core.runner

set -euo pipefail  # Exit on error, undefined variable, pipe failure

# ── Runtime parameters with default values ────────────────────────────────────
CONFIG="${1:-config/system.json}"       # System configuration file
LOG_LEVEL="${2:-INFO}"                   # Log level (DEBUG/INFO/WARNING/ERROR)
PORT="${3:-8000}"                        # API server port

# ── Python & pyenv configuration ─────────────────────────────────────────────
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}" # pyenv installation directory (default: ~/.pyenv)
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}" # Required Python version

PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}"

# ── Logging helper ───────────────────────────────────────────────────────────
log() { echo "[run] $*"; }  # Print messages with a "[run]" prefix for easier log tracking

# ── Step 1: Initialize pyenv in the current shell session (if available) ─────
# pyenv allows installing and managing multiple Python versions. If available,
# initialize it so the script can use pyenv-managed Python instead of system Python.
if [ -x "$PYENV_ROOT/bin/pyenv" ]; then
    export PYENV_ROOT  # pyenv installation directory
    export PATH="$PYENV_ROOT/bin:$PATH"  # Add pyenv to PATH
    eval "$(pyenv init -)"  # Initialize pyenv in this shell
fi

# ── Step 2: Helper to stop any process using the port ─────────────────────────
# Avoid "port already in use" errors by force-stopping any old process on the same port.
# Useful when restarting the application repeatedly or while debugging.
stop_process_on_port() {
    local port="$1"  # Port to inspect
    local pids
    
    # Get the list of PIDs listening on the TCP port (lsof -ti)
    # 2>/dev/null suppresses error messages; || true prevents exit if nothing is found
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    
    if [ -n "$pids" ]; then
        # If a process is using the port, stop each one
        for pid in $pids; do
            # Get the process name (ps -p $pid -o comm=) for logging
            local name
            name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            log "Stopping '$name' (PID $pid) on port $port"
            
            # Send SIGKILL (-9) to force-stop the process
            kill -9 "$pid" 2>/dev/null || true
        done
        
        # Wait for the OS to release the port socket (avoid TIME_WAIT issues)
        sleep 0.8
        log "Port $port cleared."
    fi
}

# ── Step 3: Check and prepare the Python interpreter ─────────────────────────
# Priority: .venv/bin/python (local venv) → setup if needed → python3/python (system)

VENV_PY=".venv/bin/python"  # Python path inside the local virtual environment

# If the venv does not exist, run setup_linux.sh to create it
if [ ! -f "$VENV_PY" ]; then
    log "Virtualenv not found — running setup first..."
    
    # Compute the absolute path of the current script
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SETUP_SCRIPT="$SCRIPT_DIR/setup_linux.sh"
    
    if [ -f "$SETUP_SCRIPT" ]; then
        # Run the setup script to create the venv, install pyenv, and install dependencies
        bash "$SETUP_SCRIPT"
    else
        echo "setup_linux.sh not found at: $SETUP_SCRIPT" >&2
        exit 1
    fi
fi

# If the venv is still missing (setup failed), fall back to system Python
if [ ! -f "$VENV_PY" ]; then
    log ".venv still missing — falling back to system Python."
    
    # Find python3 or python in the system PATH
    if command -v python3 &>/dev/null; then
        VENV_PY="python3"  # Prefer python3 (Python 3.x)
    elif command -v python &>/dev/null; then
        VENV_PY="python"    # Fallback python (may be Python 2 or 3)
    else
        # No Python interpreter found → error
        echo "No Python interpreter found. Install Python >= 3.10 and retry." >&2
        exit 1
    fi
fi

# ── Step 4: Stop old processes on the port (avoid port conflicts) ───────────
stop_process_on_port "$PORT"

# ── Step 5: Run the application ──────────────────────────────────────────────
# Start the CAN-HMI runner module with the selected configuration.
# Parameters:
#   --config   : JSON configuration file path
#   --log-level: Logging level (DEBUG/INFO/WARNING/ERROR)
# The API port is set in config/system.json, not via a CLI argument
log "Starting CAN-HMI on port $PORT (press Ctrl+C to stop)"
"$VENV_PY" -m src.core.runner --config "$CONFIG" --log-level "$LOG_LEVEL"
