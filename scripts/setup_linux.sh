#!/usr/bin/env bash
################################################################################
# CAN-HMI Setup Script — Linux/macOS
################################################################################
# Purpose: Prepare the development environment (Python, dependencies, venv)
# Run once after cloning the repo or whenever the environment needs to be reset
#
# Usage:
#   bash scripts/setup_linux.sh
#
# Setup steps:
#   1. Install pyenv (the Python version manager) — if missing
#   2. Install the requested Python version via pyenv — if missing
#   3. Create the .venv virtual environment (isolated Python) — if missing
#   4. Ensure pip is available in the venv (fall back to get-pip.py if needed)
#   5. Upgrade pip and install dependencies (editable + dev packages)
#
# Configurable environment variables:
#   \$PYTHON_VERSION — Python version to install (default: 3.12.3)
#   \$PYENV_ROOT     — pyenv installation directory (default: ~/.pyenv)
#
# System requirements:
#   - curl or wget (to download pyenv and get-pip.py)
#   - Build tools (gcc, make, libssl-dev, ...) — installed automatically via apt when sudo is available
#   - apt-get (on Debian/Ubuntu) — to install build dependencies automatically

set -euo pipefail  # Exit on error, undefined variable, pipe failure

# ── Python & pyenv configuration ─────────────────────────────────────────────
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}"   # Python version to install
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"     # pyenv installation directory

# ── Logging helper ───────────────────────────────────────────────────────────
log() { echo "[setup] $*"; }  # Print messages with a "[setup]" prefix for easy identification

# ── Step 1: Install pyenv (if missing) ───────────────────────────────────────
# pyenv manages multiple Python versions and helps avoid version conflicts.
# Check whether the pyenv executable exists at $PYENV_ROOT/bin/pyenv
# Prefer an existing system Python >= requested version to avoid long pyenv builds.
if command -v python3 &>/dev/null; then
    log "System python3 detected — checking version"
    if python3 - <<PY >/dev/null 2>&1
import sys
req = "${PYTHON_VERSION}"
reqt = tuple(map(int, req.split(".")))
if sys.version_info[:3] >= reqt:
    sys.exit(0)
sys.exit(1)
PY
    then
        PYTHON="$(command -v python3)"
        log "Using system Python: $PYTHON"
        SKIP_PYENV=1
    else
        log "System python3 present but older than ${PYTHON_VERSION}; will use pyenv"
        SKIP_PYENV=0
    fi
else
    SKIP_PYENV=0
fi

if [ ! -x "$PYENV_ROOT/bin/pyenv" ]; then
    log "pyenv not found — installing pyenv to $PYENV_ROOT"
    log "(This may take a few seconds — downloading and running pyenv installer)"
    
    # Download and run the installer from https://pyenv.run/
    # Prefer curl (faster), fall back to wget if curl is unavailable
    if command -v curl &>/dev/null; then
        curl -fsSL https://pyenv.run | bash  # -fsSL: fail on error, silent, show errors, location follow
    elif command -v wget &>/dev/null; then
        wget -qO- https://pyenv.run | bash  # -qO-: quiet, output to stdout
    else
        echo "curl or wget is required to install pyenv." >&2
        exit 1
    fi
    
    log "pyenv installed successfully to $PYENV_ROOT"
fi

# ── Step 2: Initialize pyenv in the current shell session ───────────────────
# Required so this script can use pyenv commands (pyenv versions, pyenv install)
export PYENV_ROOT              # Let pyenv know its installation directory
export PATH="$PYENV_ROOT/bin:$PATH"  # Add pyenv to PATH so the command can be found
# Initialize pyenv only when it's available and we didn't decide to use system python
if [ "$SKIP_PYENV" != "1" ] && [ -x "$PYENV_ROOT/bin/pyenv" ]; then
    eval "$(pyenv init -)"
fi

# ── Step 3: Install the requested Python version via pyenv (if missing) ─────
# Check whether Python $PYTHON_VERSION is already installed (pyenv versions --bare)
# If not, compile it from source (takes a few minutes) and install build dependencies if needed
if ! pyenv versions --bare | grep -qx "$PYTHON_VERSION"; then
    log "Installing Python $PYTHON_VERSION via pyenv (compiling from source — this may take a few minutes)"
    
    # Install build dependencies on Debian/Ubuntu when apt-get and sudo are available
    # These libraries are required to compile Python from source
    if command -v apt-get &>/dev/null && command -v sudo &>/dev/null; then
        log "Installing build dependencies via apt-get (requires sudo — you may need to enter password)"
        
        # Update the package index
        sudo apt-get update || true
        
        # Install build tools and libraries (libc headers, SSL, zlib, readline, sqlite, etc.)
        # -y: auto-yes, --no-install-recommends: skip recommendations, || true: ignore errors
        sudo apt-get install -y --no-install-recommends \
            build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
            libsqlite3-dev libffi-dev liblzma-dev libncursesw5-dev xz-utils \
            tk-dev curl wget 2>&1 | grep -E "(Setting up|Processing|Unpacking|^E:)" || true
        
        log "Build dependencies installed"
    fi
    
    # Install Python from source via pyenv (takes 5-10 minutes depending on the machine)
    log "Compiling Python $PYTHON_VERSION (this will take several minutes...)"  
    pyenv install "$PYTHON_VERSION"
    
    log "Python $PYTHON_VERSION installed successfully"
fi

# ── Full path to the Python executable ───────────────────────────────────────
PYTHON="$PYENV_ROOT/versions/$PYTHON_VERSION/bin/python3"

# ── Step 4: Create the local virtual environment (.venv) ─────────────────────
# A venv is an isolated environment for installing project dependencies without affecting
# the system Python. Each project should have its own venv.

if [ ! -d ".venv" ]; then
    log "Creating virtualenv (.venv) using Python $PYTHON_VERSION"
    # Create the venv with Python's venv module (equivalent to python -m venv .venv)
    "$PYTHON" -m venv .venv
    log ".venv created at $(pwd)/.venv"
fi

# ── Verify that the venv exists and is ready ─────────────────────────────────
VENV_PY=".venv/bin/python"   # Path to the Python executable inside the venv

if [ ! -f "$VENV_PY" ]; then
    log "Virtualenv python not found; attempting to recreate .venv"
    # If .venv is broken (missing the python binary), recreate it
    "$PYTHON" -m venv .venv
fi

# ── Step 5: Ensure pip is available in the venv ──────────────────────────────
# pip is the Python package manager. Some Python builds do not include pip.
# Recovery steps:
#   1. Try ensurepip (built-in Python module) — fastest option
#   2. Fallback: download get-pip.py from bootstrap.pypa.io — slower but reliable

if ! "$VENV_PY" -m pip --version &>/dev/null; then
    log "pip not found in venv — attempting to install"
    
    # Option 1: Use ensurepip (integrated Python tool)
    # -m ensurepip: Python's ensurepip module
    # --upgrade: upgrade pip to the latest version
    if "$VENV_PY" -m ensurepip --upgrade &>/dev/null; then
        log "✓ pip installed via ensurepip (built-in)"
    else
        # Option 2: Fallback — download and run get-pip.py (official bootstrapper)
        log "ensurepip unavailable — using get-pip.py bootstrapper as fallback"
        
        # Create a temporary file to store get-pip.py
        tmpfile=$(mktemp)
        
        # Download get-pip.py from bootstrap.pypa.io (official pip package source)
        if command -v curl &>/dev/null; then
            log "Downloading get-pip.py via curl..."
            curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$tmpfile"
        elif command -v wget &>/dev/null; then
            log "Downloading get-pip.py via wget..."
            wget -qO "$tmpfile" https://bootstrap.pypa.io/get-pip.py
        else
            echo "curl or wget required to install pip into venv" >&2
            exit 1
        fi
        
        # Run get-pip.py with the venv Python to install pip into the venv
        log "Installing pip from bootstrapper..."
        "$VENV_PY" "$tmpfile"
        
        # Remove the temporary file
        rm -f "$tmpfile"
        log "✓ pip installed via get-pip.py"
    fi
fi

# ── Step 6: Upgrade pip and install project dependencies ─────────────────────
# pip install -e ".[dev]":
#   -e : editable mode (symlink project → easy to code and test changes immediately)
#   .[dev] : install the current package + [dev] extras (test tools, linters, etc.)
#   Versions and dependencies are defined in pyproject.toml

log "Upgrading pip to latest version..."
"$VENV_PY" -m pip install --upgrade pip

log "Installing project dependencies (editable + dev packages)..."
log "This may take a minute or two depending on network and disk speed..."
"$VENV_PY" -m pip install -e ".[dev]"

# ── Step 7: Completion ───────────────────────────────────────────────────────
log ""
log "════════════════════════════════════════════════════════════════"
log "✓ Setup complete!"
log "════════════════════════════════════════════════════════════════"
log ""
log "Next steps:"
log "  1. Run the app:  bash scripts/run_linux.sh"
log "  2. Open browser: http://localhost:8000"
log "  3. View API:     http://localhost:8000/docs"
log ""
log "Notes:"
log "  • Virtual environment: $(pwd)/.venv"
log "  • Python version: $PYTHON_VERSION"
log "  • Config file: $(pwd)/config/system.json"
log ""
