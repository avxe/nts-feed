#!/bin/bash
# Docker entrypoint: Chooses between development (Flask) and production (gunicorn) servers
# 
# Behavior:
#   - FLASK_DEBUG=1 or FLASK_ENV=development → Flask dev server (hot reload)
#   - Otherwise → Gunicorn production server (multi-worker, performant)

set -euo pipefail

# Default values (all vars need defaults because of set -u)
HOST="${FLASK_RUN_HOST:-0.0.0.0}"
PORT="${FLASK_RUN_PORT:-5555}"
WORKERS="${GUNICORN_WORKERS:-1}"
THREADS="${GUNICORN_THREADS:-4}"
TIMEOUT="${GUNICORN_TIMEOUT:-120}"
FLASK_DEBUG="${FLASK_DEBUG:-0}"
FLASK_ENV="${FLASK_ENV:-production}"

if [ "$FLASK_DEBUG" = "1" ] || [ "$FLASK_ENV" = "development" ]; then
    echo "🔧 Starting Flask development server (debug mode, auto-reload)..."
    exec python -m flask run --host="$HOST" --port="$PORT" --reload
else
    echo "🚀 Starting Gunicorn production server..."
    echo "   Workers: $WORKERS | Threads: $THREADS | Timeout: ${TIMEOUT}s | Bind: $HOST:$PORT"
    exec gunicorn \
        --bind "$HOST:$PORT" \
        --worker-class gthread \
        --workers "$WORKERS" \
        --threads "$THREADS" \
        --timeout "$TIMEOUT" \
        --graceful-timeout 30 \
        --max-requests 1000 \
        --max-requests-jitter 50 \
        --preload \
        --access-logfile - \
        --error-logfile - \
        --capture-output \
        --enable-stdio-inheritance \
        "nts_feed.app:create_app()"
fi
