#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="${NTS_FEED_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"
OPENSSL_BIN="${OPENSSL_BIN:-openssl}"
PYTHON_BIN="${PYTHON_BIN:-python3}"

cd "$ROOT_DIR"

mkdir -p config/nginx/ssl

prepare_storage_layout() {
    local script_repo_root
    script_repo_root="$(cd "$(dirname "$0")/.." && pwd)"

    if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
        printf 'Python 3 is required to prepare runtime storage.\n' >&2
        exit 1
    fi

    NTS_FEED_ROOT="$ROOT_DIR" NTS_FEED_SCRIPT_REPO_ROOT="$script_repo_root" "$PYTHON_BIN" - <<'PY'
import importlib
import os
import sys
import types
from pathlib import Path

repo_root = os.environ["NTS_FEED_SCRIPT_REPO_ROOT"]
package_root = Path(repo_root) / "nts_feed"
storage_root = package_root / "storage"

pkg = types.ModuleType("nts_feed")
pkg.__path__ = [str(package_root)]
sys.modules["nts_feed"] = pkg

storage_pkg = types.ModuleType("nts_feed.storage")
storage_pkg.__path__ = [str(storage_root)]
sys.modules["nts_feed.storage"] = storage_pkg

module = importlib.import_module("nts_feed.runtime_paths")

result = module.ensure_runtime_layout()
for backed_up in result["backed_up"]:
    print(f"Backed up legacy runtime path to {backed_up}")
for moved in result["moved"]:
    print(f"Migrated {moved}")
for created in result["created"]:
    print(f"Created {created}")
print(f"Storage root ready at {module.storage_root()}")
PY
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

prepare_storage_layout
generate_local_certs
printf 'Runtime scaffolding is ready.\n'
