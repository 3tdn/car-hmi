#!/usr/bin/env bash
# Refresh source edits using dependencies and systemd units installed by full deploy.
set -euo pipefail

usage() {
    cat <<'HELP'
Usage: bash scripts/dev_deploy_linux.sh [--backend-only|--frontend-only]

Default: build the frontend with existing dependencies, then restart both services.
--backend-only   Restart Python after backend source edits; skip frontend work.
--frontend-only  Build/publish dist and restart only the frontend service.

Run setup/full deploy first. Dependency, backend .env, unit, Node path or project
path changes require setup/full deploy again. Frontend .env edits are rebuilt.
HELP
}
BACKEND=true
FRONTEND=true
case "${1:-}" in
    '') ;;
    --backend-only) FRONTEND=false ;;
    --frontend-only) BACKEND=false ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; exit 2 ;;
esac
if (( $# > 1 )); then usage >&2; exit 2; fi

log() { echo "[dev-deploy] $*"; }
die() { echo "[dev-deploy] ERROR: $*" >&2; exit 1; }
root() {
    if [[ "$EUID" -eq 0 ]]; then "$@"; else sudo "$@"; fi
}
command -v systemctl >/dev/null || die 'systemd is required; use scripts/run_linux.sh otherwise.'
source "$(dirname "${BASH_SOURCE[0]}")/linux_common.sh"
started=$SECONDS
services=()
BUILD_DIR=''
COMPLETE=false

cleanup() {
    local result=$?
    trap - EXIT
    if [[ -n "$BUILD_DIR" ]]; then
        if [[ "$COMPLETE" != true && -d "$BUILD_DIR/previous-dist" ]]; then
            log 'Restoring the previous frontend dist.'
            rm -rf -- "$FRONTEND_DIR/dist"
            if ! mv -- "$BUILD_DIR/previous-dist" "$FRONTEND_DIR/dist"; then
                echo "[dev-deploy] ERROR: Restore dist manually from $BUILD_DIR/previous-dist" >&2
                exit 1
            fi
        fi
        rm -rf -- "$BUILD_DIR"
    fi
    exit "$result"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

check_service_path() {
    local unit="$1" expected="$2" actual
    expected="$(cd -- "$expected" && pwd -P)"
    [[ "$(systemctl show "$unit" --property=LoadState --value)" == loaded ]] ||
        die "$unit is not installed. Run scripts/deploy_linux.sh first."
    actual="$(systemctl show "$unit" --property=WorkingDirectory --value)" ||
        die "Cannot read $unit."
    actual="$(cd -- "$actual" 2>/dev/null && pwd -P)" ||
        die "$unit has a missing working directory. Run scripts/deploy_linux.sh again."
    [[ "$actual" == "$expected" ]] ||
        die "$unit points to a different project directory. Run scripts/deploy_linux.sh again."
}

if [[ "$BACKEND" == true ]]; then
    [[ -x "$PROJECT_DIR/.venv/bin/can-hmi" ]] || die 'Backend environment missing. Run scripts/setup_linux.sh first.'
    check_service_path can-hmi.service "$PROJECT_DIR"
    services+=(can-hmi.service)
fi
if [[ "$FRONTEND" == true ]]; then
    require_frontend_runtime
    FRONTEND_DIR="$(cd "$FRONTEND_DIR" && pwd -P)"
    check_service_path can-hmi-frontend.service "$FRONTEND_DIR"
    [[ -f "$FRONTEND_DIR/node_modules/vite/bin/vite.js" && -f "$FRONTEND_DIR/dist/index.html" ]] ||
        die 'Frontend dependencies/dist missing. Run setup/full deploy first.'
    command -v setfacl >/dev/null && command -v runuser >/dev/null ||
        die 'setfacl and runuser are required. Run scripts/deploy_linux.sh first.'
    frontend_user="$(systemctl show can-hmi-frontend.service --property=User --value)"
    frontend_user="${frontend_user:-root}"

    BUILD_DIR="$(mktemp -d "$FRONTEND_DIR/.dev-deploy.XXXXXX")"
    log 'Building frontend with existing dependencies...'
    build_started=$SECONDS
    npm --prefix "$FRONTEND_DIR" run build -- --outDir "$BUILD_DIR/dist"
    [[ -f "$BUILD_DIR/dist/index.html" ]] || die 'Frontend build did not produce dist/index.html.'
    log "Build completed in $((SECONDS - build_started))s. Preparing dist permissions..."
    # New assets need access; existing node_modules and parent ACLs were set by full deploy.
    root setfacl -m "u:${frontend_user}:--x" -- "$BUILD_DIR"
    root setfacl -R -P -m "u:${frontend_user}:rX" -- "$BUILD_DIR/dist"
    root runuser -u "$frontend_user" -- test -r "$BUILD_DIR/dist/index.html"
    mv -- "$FRONTEND_DIR/dist" "$BUILD_DIR/previous-dist"
    mv -- "$BUILD_DIR/dist" "$FRONTEND_DIR/dist"
    services+=(can-hmi-frontend.service)
fi

log "Restarting ${services[*]}..."
root systemctl restart "${services[@]}"
sleep 1
for unit in "${services[@]}"; do
    root systemctl is-active --quiet "$unit" || die "$unit is not active; inspect journalctl -u $unit."
done
COMPLETE=true
log "Completed in $((SECONDS - started))s; selected systemd services are active."
