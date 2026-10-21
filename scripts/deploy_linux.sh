#!/usr/bin/env bash
# Install, enable and start the backend and optional npm frontend on Linux/systemd.
# Usage: bash scripts/deploy_linux.sh [--status|--uninstall]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SYSTEMD_DIR=/etc/systemd/system
BACKEND_SERVICE=can-hmi.service
FRONTEND_SERVICE=can-hmi-frontend.service
SERVICE_USER="${SUDO_USER:-$(id -un)}"
log() { echo "[deploy] $*"; }
warn() { echo "[deploy] WARNING: $*" >&2; }
root() {
    if [[ "$EUID" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}

if [[ ! -d /run/systemd/system ]]; then
    echo 'systemd must be running on the Linux host to install boot services.' >&2
    echo 'For a container/demo use: bash scripts/run_linux.sh' >&2
    exit 1
fi

case "${1:-}" in
    --status)
        root systemctl status "$BACKEND_SERVICE" --no-pager || exit $?
        if [[ -f "$SYSTEMD_DIR/$FRONTEND_SERVICE" ]]; then
            root systemctl status "$FRONTEND_SERVICE" --no-pager || exit $?
        else
            warn 'Frontend service is not installed.'
        fi
        exit 0 ;;
    --uninstall)
        root systemctl disable --now "$BACKEND_SERVICE" "$FRONTEND_SERVICE" || true
        root rm -f "$SYSTEMD_DIR/$BACKEND_SERVICE" "$SYSTEMD_DIR/$FRONTEND_SERVICE"
        root systemctl daemon-reload
        log 'Removed backend and frontend services.'
        exit 0 ;;
    '') ;;
    *) echo "Usage: $0 [--status|--uninstall]" >&2; exit 2 ;;
esac

source "$PROJECT_DIR/scripts/linux_common.sh"
source "$PROJECT_DIR/scripts/linux_frontend_access.sh"
VENV_BIN="$PROJECT_DIR/.venv/bin/can-hmi"
if [[ ! -x "$VENV_BIN" ]]; then
    echo 'Backend environment missing. Run scripts/setup_linux.sh first.' >&2
    exit 1
fi
if [[ ! -f "$PROJECT_DIR/config/system.json" && ! -f "$PROJECT_DIR/config/system_bk.json" ]]; then
    echo 'Missing config/system.json and config/system_bk.json.' >&2
    exit 1
fi

if ! command -v ffplay >/dev/null || ! command -v ffprobe >/dev/null; then
    if ! command -v apt-get >/dev/null; then
        echo 'Install ffmpeg (ffplay and ffprobe) with your package manager first.' >&2
        exit 1
    fi
    root apt-get update
    root apt-get install -y --no-install-recommends ffmpeg
fi
if getent group audio >/dev/null && ! id -nG "$SERVICE_USER" | tr ' ' '\n' | grep -qx audio; then
    root usermod -aG audio "$SERVICE_USER"
fi

mkdir -p "$PROJECT_DIR/data" "$PROJECT_DIR/logs"
GENERATED_DIR="$(mktemp -d)"
trap 'rm -rf "$GENERATED_DIR"' EXIT

render_unit() {
    local service_name="$1"
    shift
    "$PROJECT_DIR/.venv/bin/python" - "$PROJECT_DIR" "$GENERATED_DIR" "$service_name" "$@" <<'PY'
import sys
from pathlib import Path

project, destination, service_name, *pairs = sys.argv[1:]
if len(pairs) % 2:
    raise ValueError('Unit template values must be key/value pairs')
values = {'PROJECT_DIR': project, **dict(zip(pairs[0::2], pairs[1::2]))}
template = Path(project) / 'deploy' / service_name
content = template.read_text()
for key, value in values.items():
    if '\n' in value or '\r' in value or '$' in value:
        raise ValueError(f'Unsafe value for {key}')
    raw = value.replace('%', '%%')
    quoted = raw.replace('\\', '\\\\').replace('"', '\\"')
    content = '\n'.join(
        line.replace(
            '@@' + key + '@@',
            raw if line.startswith(('User=', 'WorkingDirectory=', 'EnvironmentFile=')) else quoted,
        )
        for line in content.split('\n')
    )
(Path(destination) / service_name).write_text(content)
PY
}

log 'Deploying backend service first...'
render_unit "$BACKEND_SERVICE" SERVICE_USER "$SERVICE_USER"
systemd-analyze verify "$GENERATED_DIR/$BACKEND_SERVICE"
root install -m 644 "$GENERATED_DIR/$BACKEND_SERVICE" "$SYSTEMD_DIR/$BACKEND_SERVICE"
root systemctl daemon-reload
root systemctl enable "$BACKEND_SERVICE"
root systemctl restart "$BACKEND_SERVICE"
sleep 2
if ! root systemctl is-active --quiet "$BACKEND_SERVICE"; then
    root journalctl -u "$BACKEND_SERVICE" -n 30 --no-pager
    exit 1
fi
log 'Backend is installed, enabled, and running.'

deploy_frontend() {
    require_frontend_runtime || return $?
    [[ -f "$PROJECT_DIR/deploy/$FRONTEND_SERVICE" ]] || {
        warn "Frontend unit template is missing: $PROJECT_DIR/deploy/$FRONTEND_SERVICE"
        return 1
    }

    local node_bin frontend_dir frontend_user backend_url config_path
    node_bin="$(readlink -f -- "$(command -v node)")" || return $?
    node -e 'if (Number(process.versions.node.split(".")[0]) < 20) process.exit(1)' || {
        warn 'Node.js >= 20 is required for the frontend; skipping frontend deployment.'
        return 1
    }
    frontend_dir="$(cd "$FRONTEND_DIR" && pwd -P)" || return $?
    frontend_user=car-hmi-frontend
    if ! getent passwd "$frontend_user" >/dev/null; then
        root useradd --system --no-create-home --shell /usr/sbin/nologin "$frontend_user" || return $?
    fi
    npm --prefix "$frontend_dir" ci --omit=dev || return $?

    if ! command -v setfacl >/dev/null; then
        if ! command -v apt-get >/dev/null; then
            warn 'Install the acl package (setfacl) to grant frontend service access.'
            return 1
        fi
        root apt-get update || return $?
        root apt-get install -y --no-install-recommends acl || return $?
    fi
    if ! command -v runuser >/dev/null; then
        warn 'Install util-linux (runuser) to verify frontend service access.'
        return 1
    fi
    grant_frontend_access "$frontend_user" "$frontend_dir" "$node_bin" || return $?
    root runuser -u "$frontend_user" -- "$node_bin" --check "$frontend_dir/server.mjs" || return $?

    config_path="$PROJECT_DIR/config/system.json"
    [[ -f "$config_path" ]] || config_path="$PROJECT_DIR/config/system_bk.json"
    backend_url="$("$PROJECT_DIR/.venv/bin/python" - "$config_path" "${PORT:-}" <<'PY'
import json
import sys

config = json.loads(open(sys.argv[1], encoding='utf-8').read())
api = config.get('api', {})
host = api.get('host') or '127.0.0.1'
if host in ('0.0.0.0', '::', '[::]'):
    host = '127.0.0.1'
if ':' in host and not host.startswith('['):
    host = f'[{host}]'
port = sys.argv[2] or api.get('port') or 8000
print(f'http://{host}:{port}')
PY
    )" || return $?

    render_unit "$FRONTEND_SERVICE" \
        SERVICE_USER "$frontend_user" \
        FRONTEND_DIR "$frontend_dir" \
        NODE_BIN "$node_bin" \
        ENV_FILE "$PROJECT_DIR/.env" \
        BACKEND_URL "$backend_url" || return $?
    systemd-analyze verify "$GENERATED_DIR/$FRONTEND_SERVICE" || return $?
    root install -m 644 "$GENERATED_DIR/$FRONTEND_SERVICE" "$SYSTEMD_DIR/$FRONTEND_SERVICE" || return $?
    root systemctl daemon-reload || return $?
    root systemctl enable "$FRONTEND_SERVICE" || return $?
    root systemctl restart "$FRONTEND_SERVICE" || return $?
    sleep 2
    if ! root systemctl is-active --quiet "$FRONTEND_SERVICE"; then
        root journalctl -u "$FRONTEND_SERVICE" -n 30 --no-pager
        return 1
    fi
    root systemctl status "$FRONTEND_SERVICE" --no-pager -l
}

if deploy_frontend; then
    log "Frontend is installed, enabled, and running at http://localhost:${CAR_HMI_FRONTEND_PORT:-5173}"
else
    frontend_status=$?
    warn "Frontend deployment failed (exit $frontend_status); keeping the backend running and continuing."
fi

if ! root systemctl is-active --quiet "$BACKEND_SERVICE"; then
    root journalctl -u "$BACKEND_SERVICE" -n 30 --no-pager
    exit 1
fi
root systemctl status "$BACKEND_SERVICE" --no-pager -l
if root systemctl is-active --quiet "$FRONTEND_SERVICE"; then
    log 'Frontend service is active.'
else
    warn 'Frontend service is not active; backend deployment completed successfully.'
fi
log 'Logs: sudo journalctl -u can-hmi -u can-hmi-frontend -f'
