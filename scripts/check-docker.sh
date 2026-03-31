#!/usr/bin/env bash

set -euo pipefail

DOCKER_BIN="${DOCKER_BIN:-docker}"
ROOT_DIR="${NTS_FEED_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
REQUIRED_PORTS="${NTS_FEED_REQUIRED_PORTS-80 443}"
COMPOSE_ARGS=(-f docker-compose.yml)

if [ "${1:-}" = "--dev" ]; then
    COMPOSE_ARGS+=(-f docker-compose.dev.yml)
fi

issues=()
warnings=()

add_issue() {
    issues+=("$1")
}

add_warning() {
    warnings+=("$1")
}

check_port() {
    local port="$1"

    if command -v python3 >/dev/null 2>&1; then
        if ! python3 - "$port" <<'PY'
import socket
import sys

port = int(sys.argv[1])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.settimeout(0.25)
    sys.exit(0 if sock.connect_ex(("127.0.0.1", port)) == 0 else 1)
PY
        then
            return 1
        fi
        return 0
    fi

    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
        return $?
    fi

    return 2
}

project_nginx_running() {
    "$DOCKER_BIN" compose "${COMPOSE_ARGS[@]}" ps --services --status running 2>/dev/null | grep -qx 'nginx'
}

if ! command -v "$DOCKER_BIN" >/dev/null 2>&1; then
    add_issue "Docker CLI not found. Install Docker Desktop (or Docker Engine with the Compose plugin) and try again."
else
    if ! "$DOCKER_BIN" compose version >/dev/null 2>&1; then
        add_issue "Docker Compose v2 is not available. Install Docker Desktop or the docker-compose-plugin package."
    fi

    if ! "$DOCKER_BIN" buildx version >/dev/null 2>&1; then
        add_issue "Docker buildx plugin is not available. Update Docker Desktop or install the Docker buildx plugin before running compose builds."
    fi

    if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
        add_issue "Docker daemon is not running. Start Docker Desktop and wait for it to finish booting, or start your Docker daemon manually."
    fi
fi

cd "$ROOT_DIR"

if [ "${#issues[@]}" -eq 0 ]; then
    if [ -f .env ] && ! "$DOCKER_BIN" compose "${COMPOSE_ARGS[@]}" config >/dev/null 2>&1; then
        add_issue "Docker Compose could not resolve the current configuration. Check .env values and compose file syntax."
    fi

    for port in $REQUIRED_PORTS; do
        if check_port "$port"; then
            if project_nginx_running; then
                add_warning "Port $port is already in use by the running NTS Feed stack. Reusing the existing port binding."
            else
                add_issue "Port $port is already in use on this machine. Stop the process using it or change the nginx host port mapping before starting NTS Feed."
            fi
        elif [ "$?" -eq 2 ]; then
            add_warning "Could not verify whether port $port is free because neither python3 nor lsof is available."
        fi
    done
fi

if [ "${#issues[@]}" -gt 0 ]; then
    printf '\nNTS Feed Docker preflight failed:\n\n' >&2
    for issue in "${issues[@]}"; do
        printf '  - %s\n' "$issue" >&2
    done
    printf '\nNext step:\n' >&2
    printf '  - Fix the items above, then rerun `make quickstart`.\n' >&2
    printf '  - If you do not want Docker, use the Python source install in README.md.\n\n' >&2
    exit 1
fi

if [ "${#warnings[@]}" -gt 0 ]; then
    printf 'NTS Feed Docker preflight warnings:\n'
    for warning in "${warnings[@]}"; do
        printf '  - %s\n' "$warning"
    done
fi

printf 'Docker prerequisites look good.\n'
