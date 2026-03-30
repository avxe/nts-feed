#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="${NTS_FEED_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
MODE="generic"

if [ "${1:-}" = "--for-docker" ]; then
    MODE="docker"
fi

cd "$ROOT_DIR"

issues=()

add_issue() {
    issues+=("$1")
}

read_env_value() {
    local key="$1"
    local line value

    line="$(grep -E "^${key}=" .env 2>/dev/null | tail -n 1 || true)"
    value="${line#*=}"
    value="${value%$'\r'}"
    printf '%s' "$value"
}

if [ ! -f .env ]; then
    add_issue ".env does not exist. Run \`make setup\` or \`make quickstart\` first."
else
    secret_key="$(read_env_value SECRET_KEY)"
    if [ -z "$secret_key" ] || [ "$secret_key" = "your_secret_key_here" ]; then
        add_issue "SECRET_KEY is missing or still set to a placeholder value. Re-run \`make setup\` or edit .env."
    fi

    if [ "$MODE" = "docker" ]; then
        auto_add_dir_host="$(read_env_value AUTO_ADD_DIR_HOST)"
        music_dir_host="$(read_env_value MUSIC_DIR_HOST)"

        if [ -n "$auto_add_dir_host" ] && [ "$auto_add_dir_host" = "/absolute/path/to/Automatically Add to Music.localized" ]; then
            add_issue "AUTO_ADD_DIR_HOST still points at the sample placeholder from env.example. Remove it or replace it with a real path before running Docker."
        fi

        if [ -n "$music_dir_host" ] && [ "$music_dir_host" = "/absolute/path/to/Music/Compilations/NTS" ]; then
            add_issue "MUSIC_DIR_HOST still points at the sample placeholder from env.example. Remove it or replace it with a real path before running Docker."
        fi
    fi
fi

if [ "${#issues[@]}" -gt 0 ]; then
    printf '\nNTS Feed environment check failed:\n\n' >&2
    for issue in "${issues[@]}"; do
        printf '  - %s\n' "$issue" >&2
    done
    printf '\nNext step:\n' >&2
    printf '  - Fix the items above, then rerun your command.\n\n' >&2
    exit 1
fi

printf 'Environment file looks good.\n'
