#!/usr/bin/env bash
################################################################################
# CAN-HMI Setup Script — Linux/macOS
################################################################################
# Mục đích: Chuẩn bị môi trường phát triển (Python, dependencies, venv)
# Chạy 1 lần khi clone repo hoặc khi cần reset môi trường
#
# Cách dùng:
#   bash scripts/setup_linux.sh
#
# Các bước xử lý:
#   1. Cài đặt pyenv (công cụ quản lý Python versions) — nếu chưa có
#   2. Cài Python phiên bản chỉ định qua pyenv — nếu chưa có
#   3. Tạo virtual environment .venv (Python isolated) — nếu chưa có
#   4. Đảm bảo pip có sẵn trong venv (fallback qua get-pip.py nếu cần)
#   5. Nâng cấp pip và cài dependencies (editable + dev packages)
#
# Các biến môi trường có thể tùy chỉnh:
#   \$PYTHON_VERSION — Phiên bản Python cần cài (mặc định: 3.12.3)
#   \$PYENV_ROOT     — Thư mục cài pyenv (mặc định: ~/.pyenv)
#
# Yêu cầu hệ thống:
#   - curl hoặc wget (để download pyenv và get-pip.py)
#   - Build tools (gcc, make, libssl-dev, ...) — sẽ tự cài qua apt nếu có sudo
#   - apt-get (trên Debian/Ubuntu) — để cài build dependencies tự động

set -euo pipefail  # Exit on error, undefined variable, pipe failure

# ── Cấu hình Python & pyenv ──────────────────────────────────────────────────
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}"   # Phiên bản Python cần cài
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"     # Thư mục cài pyenv

# ── Hàm logging ──────────────────────────────────────────────────────────────
log() { echo "[setup] $*"; }  # In message với prefix "[setup]" để dễ nhận biết

# ── Bước 1: Cài pyenv (nếu chưa có) ──────────────────────────────────────────
# pyenv là công cụ quản lý nhiều phiên bản Python, giúp tránh conflict phiên bản.
# Kiểm tra: pyenv executable tồn tại tại $PYENV_ROOT/bin/pyenv
if [ ! -x "$PYENV_ROOT/bin/pyenv" ]; then
    log "pyenv not found — installing pyenv to $PYENV_ROOT"
    log "(This may take a few seconds — downloading and running pyenv installer)"
    
    # Tải và chạy installer từ https://pyenv.run/
    # Ưu tiên curl (nhanh hơn), fallback wget nếu curl không có
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

# ── Bước 2: Khởi tạo pyenv trong shell session hiện tại ────────────────────────
# Cần thiết để script này có thể dùng pyenv commands (pyenv versions, pyenv install)
export PYENV_ROOT              # Biến môi trường cho pyenv biết thư mục cài của nó
export PATH="$PYENV_ROOT/bin:$PATH"  # Thêm pyenv vào PATH để tìm được pyenv command
eval "$(pyenv init -)"         # Khởi tạo pyenv trong shell này (setup shim, auto-version detection)

# ── Bước 3: Cài Python phiên bản chỉ định qua pyenv (nếu chưa có) ──────────────
# Kiểm tra: Python $PYTHON_VERSION đã cài chưa (pyenv versions --bare)
# Nếu chưa có → cài từ source (mất vài phút) + cài build dependencies nếu cần
if ! pyenv versions --bare | grep -qx "$PYTHON_VERSION"; then
    log "Installing Python $PYTHON_VERSION via pyenv (compiling from source — this may take a few minutes)"
    
    # Cài build dependencies trên Debian/Ubuntu nếu có apt-get và sudo
    # Những thư viện này cần thiết để compile Python từ source
    if command -v apt-get &>/dev/null && command -v sudo &>/dev/null; then
        log "Installing build dependencies via apt-get (requires sudo — you may need to enter password)"
        
        # Update package index
        sudo apt-get update || true
        
        # Cài build tools và libraries (libc headers, SSL, zlib, readline, sqlite, etc.)
        # -y: auto-yes, --no-install-recommends: skip recommendations, || true: bỏ qua lỗi
        sudo apt-get install -y --no-install-recommends \
            build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev \
            libsqlite3-dev libffi-dev liblzma-dev libncursesw5-dev xz-utils \
            tk-dev curl wget 2>&1 | grep -E "(Setting up|Processing|Unpacking|^E:)" || true
        
        log "Build dependencies installed"
    fi
    
    # Cài Python từ source qua pyenv (mất 5-10 phút tùy máy)
    log "Compiling Python $PYTHON_VERSION (this will take several minutes...)"  
    pyenv install "$PYTHON_VERSION"
    
    log "Python $PYTHON_VERSION installed successfully"
fi

# ── Đường dẫn đầy đủ đến Python executable ────────────────────────────────────
PYTHON="$PYENV_ROOT/versions/$PYTHON_VERSION/bin/python3"

# ── Bước 4: Tạo virtual environment cục bộ (.venv) ────────────────────────────
# venv là environment cách biệt để cài dependencies của project mà không ảnh hưởng
# đến system Python. Mỗi project nên có venv riêng.

if [ ! -d ".venv" ]; then
    log "Creating virtualenv (.venv) using Python $PYTHON_VERSION"
    # Tạo venv bằng module venv của Python (tương đương python -m venv .venv)
    "$PYTHON" -m venv .venv
    log ".venv created at $(pwd)/.venv"
fi

# ── Xác minh venv tồn tại và sẵn sàng ──────────────────────────────────────────
VENV_PY=".venv/bin/python"   # Đường dẫn Python executable trong venv

if [ ! -f "$VENV_PY" ]; then
    log "Virtualenv python not found; attempting to recreate .venv"
    # Nếu .venv bị hỏng (thiếu python binary) → tạo lại
    "$PYTHON" -m venv .venv
fi

# ── Bước 5: Đảm bảo pip có sẵn trong venv ─────────────────────────────────────
# pip là trình quản lý package Python. Một số bản build Python không bao gồm pip.
# Cách khôi phục:
#   1. Thử ensurepip (built-in Python module) — nhanh nhất
#   2. Fallback: tải get-pip.py từ bootstrap.pypa.io — chậm hơn nhưng chắc chắn

if ! "$VENV_PY" -m pip --version &>/dev/null; then
    log "pip not found in venv — attempting to install"
    
    # Cách 1: Dùng ensurepip (integrated Python tool)
    # -m ensurepip: module ensurepip của Python
    # --upgrade: nâng cấp pip lên phiên bản mới nhất
    if "$VENV_PY" -m ensurepip --upgrade &>/dev/null; then
        log "✓ pip installed via ensurepip (built-in)"
    else
        # Cách 2: Fallback — tải và chạy get-pip.py (bootstrapper chính thức)
        log "ensurepip unavailable — using get-pip.py bootstrapper as fallback"
        
        # Tạo file tạm để lưu get-pip.py
        tmpfile=$(mktemp)
        
        # Tải get-pip.py từ bootstrap.pypa.io (sources chính thức pip package)
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
        
        # Chạy get-pip.py qua venv Python → cài pip vào venv
        log "Installing pip from bootstrapper..."
        "$VENV_PY" "$tmpfile"
        
        # Xóa file tạm
        rm -f "$tmpfile"
        log "✓ pip installed via get-pip.py"
    fi
fi

# ── Bước 6: Nâng cấp pip và cài project dependencies ──────────────────────────
# pip install -e ".[dev]":
#   -e : editable mode (symlink project → dễ code, test changes ngay)
#   .[dev] : cài package hiện tại + extras [dev] (test tools, linters, etc.)
#   Phiên bản và dependencies được định nghĩa trong pyproject.toml

log "Upgrading pip to latest version..."
"$VENV_PY" -m pip install --upgrade pip

log "Installing project dependencies (editable + dev packages)..."
log "This may take a minute or two depending on network and disk speed..."
"$VENV_PY" -m pip install -e ".[dev]"

# ── Bước 7: Hoàn thành ────────────────────────────────────────────────────────
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
