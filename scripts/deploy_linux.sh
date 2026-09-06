#!/usr/bin/env bash
################################################################################
# CAN-HMI Deploy Script — Linux
################################################################################
# Purpose: Install can-hmi.service into systemd and automatically fill in the
#          actual path based on the project location and current user.
#
# Usage:
#   bash scripts/deploy_linux.sh              # Install, enable, and start the service
#   bash scripts/deploy_linux.sh --uninstall  # Stop and remove the service
#   bash scripts/deploy_linux.sh --status     # View service status
#
# Requirements:
#   - Python >= 3.10 and .venv must already exist (run setup_linux.sh first)
#   - sudo permission to copy files into /etc/systemd/system/

set -euo pipefail

# ── Constants ─────────────────────────────────────────────────────────────────
SERVICE_NAME="can-hmi"
SERVICE_FILE="${SERVICE_NAME}.service"
SYSTEMD_DIR="/etc/systemd/system"
TEMPLATE="${BASH_SOURCE[0]%/*}/../deploy/${SERVICE_FILE}"

# ── Determine the project directory (the directory containing scripts/) ───────
PROJECT_DIR="$(cd "${BASH_SOURCE[0]%/*}/.." && pwd)"
SERVICE_USER="$(whoami)"

# ── Log colors ────────────────────────────────────────────────────────────────
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
    log "Stopping and disabling ${SERVICE_NAME}..."
    sudo systemctl stop    "${SERVICE_NAME}" 2>/dev/null || true
    sudo systemctl disable "${SERVICE_NAME}" 2>/dev/null || true
    sudo rm -f "${SYSTEMD_DIR}/${SERVICE_FILE}"
    sudo systemctl daemon-reload
    log "Removed ${SERVICE_NAME}."
    exit 0
fi

# ── Prerequisite checks ───────────────────────────────────────────────────────
if [[ ! -f "${TEMPLATE}" ]]; then
    err "Template not found: ${TEMPLATE}"
    exit 1
fi

VENV_BIN="${PROJECT_DIR}/.venv/bin/can-hmi"
if [[ ! -x "${VENV_BIN}" ]]; then
    err "Executable not found: ${VENV_BIN}"
    err "Run scripts/setup_linux.sh first to create the virtualenv."
    exit 1
fi

CONFIG_FILE="${PROJECT_DIR}/config/system.json"
if [[ ! -f "${CONFIG_FILE}" ]]; then
    err "Config not found: ${CONFIG_FILE}"
    exit 1
fi

# ── Create data/ and logs/ if they do not exist ───────────────────────────────
mkdir -p "${PROJECT_DIR}/data" "${PROJECT_DIR}/logs"

# ── Generate the service file from the template ───────────────────────────────
GENERATED_FILE="$(mktemp /tmp/${SERVICE_NAME}.XXXXXX.service)"
trap 'rm -f "${GENERATED_FILE}"' EXIT

sed \
    -e "s|@@PROJECT_DIR@@|${PROJECT_DIR}|g" \
    -e "s|@@SERVICE_USER@@|${SERVICE_USER}|g" \
    "${TEMPLATE}" > "${GENERATED_FILE}"

log "Generated service file:"
log "  User            = ${SERVICE_USER}"
log "  WorkingDirectory= ${PROJECT_DIR}"
log "  ExecStart       = ${VENV_BIN}"

# ── Install into systemd ──────────────────────────────────────────────────────
log "Installing ${SERVICE_FILE} into ${SYSTEMD_DIR}..."
sudo cp "${GENERATED_FILE}" "${SYSTEMD_DIR}/${SERVICE_FILE}"
sudo chmod 644 "${SYSTEMD_DIR}/${SERVICE_FILE}"
sudo systemctl daemon-reload

# ── Enable and (re)start the service ──────────────────────────────────────────
if sudo systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; then
    warn "Service is already enabled — restarting..."
    sudo systemctl restart "${SERVICE_NAME}"
else
    sudo systemctl enable --now "${SERVICE_NAME}"
fi

sleep 2

# ── Check result ──────────────────────────────────────────────────────────────
if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "✓ ${SERVICE_NAME} is running."
    sudo systemctl status "${SERVICE_NAME}" --no-pager -l
else
    err "✗ ${SERVICE_NAME} failed to start."
    sudo journalctl -u "${SERVICE_NAME}" -n 30 --no-pager
    exit 1
fi

log ""
log "Useful commands:"
log "  sudo journalctl -u ${SERVICE_NAME} -f       # Follow logs"
log "  sudo systemctl stop    ${SERVICE_NAME}       # Stop the service"
log "  bash scripts/deploy_linux.sh --status        # View status"
log "  bash scripts/deploy_linux.sh --uninstall     # Remove the service"
