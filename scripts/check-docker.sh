#!/usr/bin/env bash

set -euo pipefail

DOCKER_BIN="${DOCKER_BIN:-docker}"

issues=()

add_issue() {
    issues+=("$1")
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

printf 'Docker prerequisites look good.\n'
