#!/usr/bin/env bash
################################################################################
# CAN-HMI Deploy Script — Linux
################################################################################
# Mục đích: Cài đặt can-hmi.service vào systemd, tự động điền đường dẫn
#           thực tế dựa trên vị trí project và user hiện tại.
#
# Cách dùng:
#   bash scripts/deploy_linux.sh              # Cài, enable và start service
#   bash scripts/deploy_linux.sh --uninstall  # Dừng và gỡ service
#   bash scripts/deploy_linux.sh --status     # Xem trạng thái service
#
# Yêu cầu:
#   - Python >= 3.10 và .venv đã được tạo (chạy setup_linux.sh trước)
#   - Quyền sudo để copy file vào /etc/systemd/system/

set -euo pipefail

# ── Hằng số ──────────────────────────────────────────────────────────────────
SERVICE_NAME="can-hmi"
SERVICE_FILE="${SERVICE_NAME}.service"
SYSTEMD_DIR="/etc/systemd/system"
TEMPLATE="${BASH_SOURCE[0]%/*}/../deploy/${SERVICE_FILE}"

# ── Xác định thư mục project (thư mục chứa scripts/) ─────────────────────────
PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
SERVICE_USER="$(whoami)"

# ── Màu sắc log ──────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy]${NC} $*"; }
err()  { echo -e "${RED}[deploy]${NC} $*" >&2; }

# ── Subcommand: --status ──────────────────────────────────────────────────────
if [[ "${1:-}" == "--status" ]]; then
    sudo systemctl status "${SERVICE_NAME}" --no-pager || true
    exit 0
fi

# ── Subcommand: --uninstall ───────────────────────────────────────────────────
if [[ "${1:-}" == "--uninstall" ]]; then
    log "Dừng và vô hiệu hoá ${SERVICE_NAME}..."
    sudo systemctl stop    "${SERVICE_NAME}" 2>/dev/null || true
    sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    sudo rm -f "${SYSTEMD_DIR}/${SERVICE_FILE}"
    sudo systemctl daemon-reload
    log "Đã gỡ ${SERVICE_NAME}."
    exit 0
fi

# ── Kiểm tra điều kiện ────────────────────────────────────────────────────────
if [[ ! -f "${TEMPLATE}" ]]; then
    err "Không tìm thấy template: ${TEMPLATE}"
    exit 1
fi

VENV_BIN="${PROJECT_DIR}/.venv/bin/can-hmi"
if [[ ! -x "${VENV_BIN}" ]]; then
    err "Không tìm thấy executable: ${VENV_BIN}"
    err "Hãy chạy scripts/setup_linux.sh trước để tạo virtualenv."
    exit 1
fi

CONFIG_FILE="${PROJECT_DIR}/config/system.json"
if [[ ! -f "${CONFIG_FILE}" ]]; then
    err "Không tìm thấy config: ${CONFIG_FILE}"
    exit 1
fi

# ── Tạo thư mục data/ và logs/ nếu chưa có ───────────────────────────────────
mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/logs"

# ── Sinh file service từ template ────────────────────────────────────────────
GENERATED_FILE="$(mktemp /tmp/${SERVICE_NAME}.XXXXXX.service)"
trap 'rm -f "${GENERATED_FILE}"' EXIT

sed \
    -e "s|@@PROJECT_DIR@@|${PROJECT_DIR}|g" \
    -e "s|@@SERVICE_USER@@|${SERVICE_USER}|g" \
    "${TEMPLATE}" > "${GENERATED_FILE}"

log "Đã tạo service file:"
log "  User            = ${SERVICE_USER}"
log "  WorkingDirectory= ${PROJECT_DIR}"
log "  ExecStart       = ${VENV_BIN}"

# ── Cài vào systemd ───────────────────────────────────────────────────────────
log "Cài ${SERVICE_FILE} vào ${SYSTEMD_DIR}..."
sudo cp "${GENERATED_FILE}" "${SYSTEMD_DIR}/${SERVICE_FILE}"
sudo chmod 644 "${SYSTEMD_DIR}/${SERVICE_FILE}"
sudo systemctl daemon-reload

# ── Enable và (re)start service ───────────────────────────────────────────────
if sudo systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    warn "Service đã được enable — thực hiện restart..."
    sudo systemctl restart "${SERVICE_NAME}"
else
    sudo systemctl enable --now "${SERVICE_NAME}"
fi

sleep 2

# ── Kiểm tra kết quả ─────────────────────────────────────────────────────────
if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "✓ ${SERVICE_NAME} đang chạy."
    sudo systemctl status "${SERVICE_NAME}" --no-pager -l
else
    err "✗ ${SERVICE_NAME} không khởi động được."
    sudo journalctl -u "${SERVICE_NAME}" -n 30 --no-pager
    exit 1
fi

log ""
log "Các lệnh hữu ích:"
log "  sudo journalctl -u ${SERVICE_NAME} -f       # Theo dõi log"
log "  sudo systemctl stop    ${SERVICE_NAME}       # Dừng service"
log "  bash scripts/deploy_linux.sh --status        # Xem trạng thái"
log "  bash scripts/deploy_linux.sh --uninstall     # Gỡ service"
