#!/usr/bin/env bash
# Shared setup for Linux launchers. Relative paths always refer to the repo.
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ ! -f .env && -f .env_bk ]]; then
    cp .env_bk .env
fi
if [[ -f .env ]]; then
    set -a
    # This local operator-owned file uses shell-compatible KEY=value syntax.
    source .env
    set +a
fi
export CAR_HMI_PROJECT_DIR="$PROJECT_DIR"
export CAR_HMI_FRONTEND_PATH="${CAR_HMI_FRONTEND_PATH:-../hmi/}"
if [[ "$CAR_HMI_FRONTEND_PATH" = /* ]]; then
    FRONTEND_DIR="$CAR_HMI_FRONTEND_PATH"
else
    FRONTEND_DIR="$PROJECT_DIR/$CAR_HMI_FRONTEND_PATH"
fi

require_frontend_runtime() {
    if ! command -v node >/dev/null || ! command -v npm >/dev/null; then
        echo 'Node.js >= 20 and npm are required. Run scripts/setup_linux.sh first.' >&2
        return 1
    fi
    node -e 'if (Number(process.versions.node.split(".")[0]) < 20) { console.error("Node.js >= 20 is required"); process.exit(1); }'
    if [[ ! -f "$FRONTEND_DIR/package.json" || ! -f "$FRONTEND_DIR/server.mjs" ]]; then
        echo "Frontend project not found: $FRONTEND_DIR" >&2
        return 1
    fi
}
