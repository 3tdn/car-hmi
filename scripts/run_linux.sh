#!/usr/bin/env bash
################################################################################
# CAN-HMI Run Script — Linux/macOS
################################################################################
# Mục đích: Khởi động ứng dụng CAN-HMI (FastAPI server + signal pipeline)
# Yêu cầu: Python >= 3.10 được cài đặt (qua pyenv hoặc hệ thống)
#
# Cách dùng:
#   bash scripts/run_linux.sh                     # Dùng cấu hình mặc định (config/system.json, port 8000)
#   bash scripts/run_linux.sh config/system.json  # Chỉ định file cấu hình tùy ý
#   bash scripts/run_linux.sh config/system.json INFO 9000  # Custom config + log level + port
#
# Các tham số:
#   \$1 CONFIG    — Đường dẫn file cấu hình (mặc định: config/system.json)
#   \$2 LOG_LEVEL — Mức độ logging: DEBUG|INFO|WARNING|ERROR (mặc định: INFO)
#   \$3 PORT      — Cổng chạy API server (mặc định: 8000)
#
# Luồng xử lý:
#   1. Kiểm tra & gán giá trị tham số
#   2. Khởi động pyenv (nếu có)
#   3. Dừng process đang chiếm dụng cổng (tránh port conflict)
#   4. Kiểm tra venv — nếu không có sẽ chạy setup_linux.sh
#   5. Chạy ứng dụng qua python -m src.core.runner

set -euo pipefail  # Exit on error, undefined variable, pipe failure

# ── Tham số chạy với giá trị mặc định ────────────────────────────────────────
CONFIG="${1:-config/system.json}"       # File cấu hình hệ thống
LOG_LEVEL="${2:-INFO}"                   # Mức độ log (DEBUG/INFO/WARNING/ERROR)
PORT="${3:-8000}"                        # Cổng API server

# ── Cấu hình Python & pyenv ──────────────────────────────────────────────────
PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}" # Thư mục cài pyenv (mặc định: ~/.pyenv)
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}" # Phiên bản Python cần thiết

PYENV_ROOT="${PYENV_ROOT:-$HOME/.pyenv}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12.3}"

# ── Hàm logging ──────────────────────────────────────────────────────────────
log() { echo "[run] $*"; }  # In message với prefix "[run]" để dễ theo dõi log

# ── Bước 1: Khởi tạo pyenv trong shell session (nếu có) ────────────────────────
# pyenv cho phép cài và quản lý nhiều phiên bản Python. Nếu có sẵn, ta khởi tạo
# để script có thể dùng Python từ pyenv thay vì system Python.
if [ -x "$PYENV_ROOT/bin/pyenv" ]; then
    export PYENV_ROOT  # Thư mục cài pyenv
    export PATH="$PYENV_ROOT/bin:$PATH"  # Thêm pyenv vào PATH
    eval "$(pyenv init -)"  # Khởi tạo pyenv trong shell này
fi

# ── Bước 2: Hàm dừng process chiếm dụng cổng ─────────────────────────────────
# Tránh lỗi "port already in use" bằng cách buộc dừng process cũ trên cùng cổng.
# Hữu ích khi restart ứng dụng nhiều lần hoặc debug.
stop_process_on_port() {
    local port="$1"  # Cổng cần kiểm tra
    local pids
    
    # Lấy danh sách PID của process lắng nghe trên cổng TCP (lsof -ti)
    # 2>/dev/null tắt error message, || true giúp script không exit nếu không tìm thấy
    pids=$(lsof -ti tcp:"$port" 2>/dev/null || true)
    
    if [ -n "$pids" ]; then
        # Có process đang chiếm cổng → dừng từng process
        for pid in $pids; do
            # Lấy tên process (ps -p $pid -o comm=) để log thông tin
            local name
            name=$(ps -p "$pid" -o comm= 2>/dev/null || echo "unknown")
            log "Stopping '$name' (PID $pid) on port $port"
            
            # Gửi signal SIGKILL (-9) để buộc dừng process
            kill -9 "$pid" 2>/dev/null || true
        done
        
        # Đợi OS kịp xóa port socket (tránh TIME_WAIT)
        sleep 0.8
        log "Port $port cleared."
    fi
}

# ── Bước 3: Kiểm tra và chuẩn bị Python interpreter ──────────────────────────
# Ưu tiên: .venv/bin/python (venv cục bộ) → setup nếu cần → python3/python (system)

VENV_PY=".venv/bin/python"  # Đường dẫn Python trong virtual environment cục bộ

# Nếu venv không tồn tại → chạy setup_linux.sh để tạo
if [ ! -f "$VENV_PY" ]; then
    log "Virtualenv not found — running setup first..."
    
    # Tính toán đường dẫn tuyệt đối của script hiện tại
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    SETUP_SCRIPT="$SCRIPT_DIR/setup_linux.sh"
    
    if [ -f "$SETUP_SCRIPT" ]; then
        # Chạy setup script để tạo venv, cài pyenv, install dependencies
        bash "$SETUP_SCRIPT"
    else
        echo "setup_linux.sh not found at: $SETUP_SCRIPT" >&2
        exit 1
    fi
fi

# Nếu venv vẫn chưa có (setup thất bại) → fallback để dùng system Python
if [ ! -f "$VENV_PY" ]; then
    log ".venv still missing — falling back to system Python."
    
    # Tìm python3 hoặc python trong system PATH
    if command -v python3 &>/dev/null; then
        VENV_PY="python3"  # Ưu tiên python3 (Python 3.x)
    elif command -v python &>/dev/null; then
        VENV_PY="python"    # Fallback python (có thể là Python 2 hoặc 3)
    else
        # Không tìm thấy Python nào → lỗi
        echo "No Python interpreter found. Install Python >= 3.10 and retry." >&2
        exit 1
    fi
fi

# ── Bước 4: Dừng process cũ trên cổng (tránh port conflict) ────────────────────
stop_process_on_port "$PORT"

# ── Bước 5: Chạy ứng dụng ─────────────────────────────────────────────────────
# Khởi chạy CAN-HMI runner module với cấu hình đã xác định.
# Tham số:
#   --config   : Đường dẫn file cấu hình JSON
#   --log-level: Mức độ logging (DEBUG/INFO/WARNING/ERROR)
# Cổng API được đặt trong config/system.json, không phải tham số CLI
log "Starting CAN-HMI on port $PORT (press Ctrl+C to stop)"
"$VENV_PY" -m src.core.runner --config "$CONFIG" --log-level "$LOG_LEVEL"
