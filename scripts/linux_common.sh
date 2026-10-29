#!/usr/bin/env bash
# Shared setup for Linux launchers. Relative paths always refer to the repo.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
# Parse the complete file before applying any values. Do not source operator
# text as shell code, or let a malformed file leave partially applied overrides.
HMI_ENV_SYSTEMD_CONTENT=""
if [[ ! -f .env ]]; then
    echo '[env] ERROR: .env is missing; continuing with defaults.' >&2
else
    env_python="${PROJECT_DIR}/.venv/bin/python"
    if [[ ! -x "$env_python" ]]; then
        env_python="$(command -v python3 || true)"
    fi
    if [[ -z "$env_python" ]]; then
        echo '[env] ERROR: Cannot validate .env without Python; continuing with defaults.' >&2
    elif env_exports="$("$env_python" "$PROJECT_DIR/scripts/linux_env.py" "$PROJECT_DIR/.env")"; then
        # linux_env.py emits only validated identifiers and shlex-quoted literals.
        eval "$env_exports"
    fi
    unset env_exports env_python
fi
export CAR_HMI_PROJECT_DIR="$PROJECT_DIR"
export CAR_HMI_FRONTEND_PATH="${CAR_HMI_FRONTEND_PATH:-}"
if [[ -z "$CAR_HMI_FRONTEND_PATH" ]]; then
    FRONTEND_DIR=""
elif [[ "$CAR_HMI_FRONTEND_PATH" = /* ]]; then
    FRONTEND_DIR="$CAR_HMI_FRONTEND_PATH"
else
    FRONTEND_DIR="$PROJECT_DIR/$CAR_HMI_FRONTEND_PATH"
fi

require_frontend_runtime() {
    if [[ -z "$FRONTEND_DIR" ]]; then
        echo 'Set CAR_HMI_FRONTEND_PATH in the backend .env before starting the frontend.' >&2
        return 1
    fi
    if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
        echo 'Node.js >= 20 and npm are required. Run scripts/setup_linux.sh first.' >&2
        return 1
    fi
    node -e 'if (Number(process.versions.node.split(".")[0]) < 20) { console.error("Node.js >= 20 is required"); process.exit(1); }' || return $?
    if [[ ! -f "$FRONTEND_DIR/package.json" || ! -f "$FRONTEND_DIR/package-lock.json" ]]; then
        echo "Frontend project not found: $FRONTEND_DIR" >&2
        return 1
    fi
}

prepare_frontend() {
    npm --prefix "$FRONTEND_DIR" ci --include=dev || return $?
    npm --prefix "$FRONTEND_DIR" run build || return $?
    [[ -f "$FRONTEND_DIR/dist/index.html" ]] || {
        echo "Frontend build did not produce dist/index.html: $FRONTEND_DIR" >&2
        return 1
    }
}
