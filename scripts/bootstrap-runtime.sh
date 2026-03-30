#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="${NTS_FEED_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
OPENSSL_BIN="${OPENSSL_BIN:-openssl}"

cd "$ROOT_DIR"

mkdir -p episodes thumbnails downloads data config/nginx/ssl auto_add_dir music_dir

repair_shows_json() {
    local backup_path

    if [ -d shows.json ]; then
        backup_path="shows.json.dir-backup.$(date +%Y%m%d%H%M%S)"
        mv shows.json "$backup_path"
        printf 'Moved unexpected shows.json directory to %s\n' "$backup_path"
    fi

    if [ ! -e shows.json ]; then
        printf '{}\n' > shows.json
    fi

    if [ ! -f shows.json ]; then
        printf 'Failed to prepare shows.json as a file.\n' >&2
        exit 1
    fi
}

generate_local_certs() {
    local ssl_dir="config/nginx/ssl"

    if [ -f "$ssl_dir/fullchain.pem" ] && [ -f "$ssl_dir/privkey.pem" ]; then
        printf 'Local SSL certificates already exist.\n'
        return 0
    fi

    if ! command -v "$OPENSSL_BIN" >/dev/null 2>&1; then
        printf 'OpenSSL not found. Skipping local certificate generation; Docker init will create certificates during startup.\n'
        return 0
    fi

    if "$OPENSSL_BIN" req -x509 -nodes -days 3650 \
        -newkey rsa:2048 \
        -keyout "$ssl_dir/privkey.pem" \
        -out "$ssl_dir/fullchain.pem" \
        -subj "/CN=localhost" \
        -addext "subjectAltName=DNS:localhost,IP:127.0.0.1" >/dev/null 2>&1; then
        chmod 644 "$ssl_dir/fullchain.pem"
        chmod 600 "$ssl_dir/privkey.pem"
        printf 'Generated local SSL certificates.\n'
    else
        rm -f "$ssl_dir/fullchain.pem" "$ssl_dir/privkey.pem"
        printf 'OpenSSL failed while generating local certificates. Docker init will try again during startup.\n'
    fi
}

repair_shows_json
generate_local_certs
printf 'Runtime scaffolding is ready.\n'
