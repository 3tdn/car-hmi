#!/usr/bin/env bash
# Setup helper for Linux/macOS (Bash)
# Usage:
#   bash scripts/setup_linux.sh
#
# This script will:
#  - ensure a .venv exists (creates if missing)
#  - install editable project + dev deps: pip install -e ".[dev]"

set -euo pipefail

PYTHON="${1:-python3}"

log() { echo "[setup] $*"; }

# Find python
if ! command -v "$PYTHON" &>/dev/null; then
    if command -v python3 &>/dev/null; then
        PYTHON="python3"
    elif command -v python &>/dev/null; then
        PYTHON="python"
    else
        echo "Python not found. Install Python >= 3.10 and re-run." >&2
        exit 1
    fi
fi

# Create venv if missing
if [ ! -d ".venv" ]; then
    log "Creating virtualenv (.venv)"
    "$PYTHON" -m venv .venv
fi

VENV_PY=".venv/bin/python"
if [ ! -f "$VENV_PY" ]; then
    log "Virtualenv python not found; recreating"
    "$PYTHON" -m venv .venv
fi

# Ensure pip exists inside the venv. Some Python builds omit ensurepip, so
# try ensurepip first, then fall back to the official get-pip.py bootstrapper.
if ! "$VENV_PY" -m pip --version &>/dev/null; then
    log "pip not found in venv; attempting to install with ensurepip"
    if "$VENV_PY" -m ensurepip --upgrade &>/dev/null; then
        log "pip installed via ensurepip"
    else
        log "ensurepip failed; downloading get-pip.py to install pip"
        tmpfile=$(mktemp)
        if command -v curl &>/dev/null; then
            curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$tmpfile"
        elif command -v wget &>/dev/null; then
            wget -qO "$tmpfile" https://bootstrap.pypa.io/get-pip.py
        else
            echo "curl or wget required to install pip into venv" >&2
            exit 1
        fi
        "$VENV_PY" "$tmpfile"
        rm -f "$tmpfile"
    fi
fi

# Upgrade pip and install dependencies
log "Upgrading pip and installing dependencies (editable + dev)"
"$VENV_PY" -m pip install --upgrade pip
"$VENV_PY" -m pip install -e ".[dev]"

log "Setup complete. Run bash scripts/run_linux.sh to start the app."
