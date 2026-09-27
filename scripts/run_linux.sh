#!/usr/bin/env bash
################################################################################
# CAN-HMI Run Script — Linux/macOS
################################################################################
# Purpose: Start the CAN-HMI application (FastAPI server + signal pipeline)
# Requirement: Python >= 3.11 must be installed (via pyenv or system Python)
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
#   3. Let the runner reject a busy port before opening CAN resources
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
        echo "No Python interpreter found. Install Python >= 3.11 and retry." >&2
        exit 1
    fi
fi

# The runner reserves the port before opening CAN. A conflict exits with a clear
# error and leaves the existing listener running; stop that service explicitly.

# ── Step 5: Run the application ──────────────────────────────────────────────
# Start the CAN-HMI runner module with the selected configuration.
# Parameters:
#   --config   : JSON configuration file path
#   --log-level: Logging level (DEBUG/INFO/WARNING/ERROR)
# PORT overrides the API port from the configuration for this process.
log "Starting CAN-HMI on port $PORT (press Ctrl+C to stop)"
while true; do
    # Export the requested launcher port so apply_environment_overrides() and
    # the port preflight use the same value that was inspected above.
    if PORT="$PORT" "$VENV_PY" -m src.core.runner --config "$CONFIG" --log-level "$LOG_LEVEL"; then
        exit_code=0
    else
        # Capture the runner status inside the else branch. The exit status of
        # an `if` statement with no matching branch is 0, which would lose the
        # dedicated reboot code if `$?` were read after `fi`.
        exit_code=$?
    fi

    if [[ "$exit_code" -eq 0 ]]; then
        exit 0
    fi
    if [[ "$exit_code" -ne 75 ]]; then
        exit "$exit_code"
    fi

    log "Car-HMI reboot requested; restarting in 1 second..."
    sleep 1
done
