#!/usr/bin/env bash
# =============================================================================
# NTS Feed — Interactive Setup Wizard
# =============================================================================
# Generates a .env file with user-selected features and API keys.
# Run: ./setup.sh  or  make setup
#
# All integrations are optional. The app works with zero API keys —
# features simply degrade gracefully when a key is absent.
# =============================================================================

set -euo pipefail

# Always run from the project root (where this script lives)
cd "$(dirname "$0")"

FORCE_OVERWRITE=0
NONINTERACTIVE=0

for arg in "$@"; do
    case "$arg" in
        --force)
            FORCE_OVERWRITE=1
            ;;
        --defaults|--non-interactive)
            NONINTERACTIVE=1
            ;;
        *)
            printf "Unknown option: %s\n" "$arg" >&2
            printf "Usage: ./setup.sh [--force] [--defaults|--non-interactive]\n" >&2
            exit 1
            ;;
    esac
done

if [ ! -r /dev/tty ]; then
    NONINTERACTIVE=1
fi

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BOLD='\033[1m'
DIM='\033[2m'
CYAN='\033[36m'
GREEN='\033[32m'
YELLOW='\033[33m'
RED='\033[31m'
RESET='\033[0m'

info()  { printf "${CYAN}%s${RESET}\n" "$1"; }
ok()    { printf "${GREEN}%s${RESET}\n" "$1"; }
warn()  { printf "${YELLOW}%s${RESET}\n" "$1"; }
dim()   { printf "${DIM}%s${RESET}\n" "$1"; }

ask_yn() {
    # Usage: ask_yn "prompt" default
    # default: y or n
    local prompt="$1" default="${2:-y}"
    local hint
    if [ "$NONINTERACTIVE" -eq 1 ]; then
        [ "$default" = "y" ]
        return
    fi
    if [ "$default" = "y" ]; then hint="[Y/n]"; else hint="[y/N]"; fi
    printf "${BOLD}  %s %s: ${RESET}" "$prompt" "$hint"
    read -r answer </dev/tty
    answer="${answer:-$default}"
    case "$answer" in
        [Yy]*) return 0 ;;
        *)     return 1 ;;
    esac
}

ask_value() {
    # Usage: ask_value "prompt" [default]
    local prompt="$1" default="${2:-}"
    if [ "$NONINTERACTIVE" -eq 1 ]; then
        echo "$default"
        return
    fi
    if [ -n "$default" ]; then
        printf "${BOLD}  %s ${DIM}[%s]${RESET}${BOLD}: ${RESET}" "$prompt" "$default"
    else
        printf "${BOLD}  %s: ${RESET}" "$prompt"
    fi
    read -r value </dev/tty
    echo "${value:-$default}"
}

generate_secret() {
    # Try python3 first, fall back to openssl, then /dev/urandom
    python3 -c "import secrets; print(secrets.token_hex(32))" 2>/dev/null \
        || openssl rand -hex 32 2>/dev/null \
        || head -c 32 /dev/urandom | xxd -p 2>/dev/null | tr -d '\n' \
        || echo "CHANGE-ME-$(date +%s)-PLEASE-REGENERATE"
}

# Escape a value for safe inclusion in the .env heredoc.
# Prefixes $ and ` with backslashes so bash doesn't expand them.
escape_env() {
    printf '%s' "$1" | sed -e 's/\\/\\\\/g' -e 's/\$/\\$/g' -e 's/`/\\`/g'
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------

echo ""
printf "${BOLD}${CYAN}"
echo "  ┌─────────────────────────────────────┐"
echo "  │       NTS Feed — Setup            │"
echo "  └─────────────────────────────────────┘"
printf "${RESET}"
echo ""
dim "  This wizard creates a .env file with your configuration."
dim "  All integrations are optional — press Enter to skip any."
echo ""

# ---------------------------------------------------------------------------
# Check for existing .env
# ---------------------------------------------------------------------------

ENV_FILE=".env"

if [ -f "$ENV_FILE" ]; then
    warn "  An existing .env file was found."
    if [ "$FORCE_OVERWRITE" -eq 1 ]; then
        dim "  Overwriting because --force was supplied."
    elif ! ask_yn "Overwrite it?" "n"; then
        ok "  Keeping your existing .env. No changes were made."
        exit 0
    fi
    echo ""
fi

# ---------------------------------------------------------------------------
# Secret key
# ---------------------------------------------------------------------------

info "  Generating secret key..."
SECRET_KEY="$(generate_secret)"
ok "  Done."
echo ""

# ---------------------------------------------------------------------------
# API Integrations
# ---------------------------------------------------------------------------

printf "${BOLD}${CYAN}  ── Optional Integrations ──${RESET}\n"
echo ""
dim "  All integrations are optional. Press Enter to skip."
echo ""

# Last.fm
dim "  Last.fm — enriches artist pages and metadata where available"
dim "  Get a key at: https://www.last.fm/api/account/create"
LASTFM_API_KEY="$(ask_value "Last.fm API key")"
LASTFM_API_SECRET=""
if [ -n "$LASTFM_API_KEY" ]; then
    LASTFM_API_SECRET="$(ask_value "Last.fm API secret (optional)")"
fi
echo ""

# Discogs
dim "  Discogs — adds vinyl release links to tracklist items"
dim "  Get a token at: https://www.discogs.com/settings/developers"
DISCOGS_TOKEN="$(ask_value "Discogs token")"
echo ""

# YouTube
dim "  YouTube — enables inline track playback"
dim "  Get a key at: https://console.cloud.google.com/apis/credentials"
YOUTUBE_API_KEY="$(ask_value "YouTube API key")"
echo ""

# ---------------------------------------------------------------------------
# Write .env
# ---------------------------------------------------------------------------

info "  Writing .env file..."

# Escape user-supplied values so $, `, and \ in API keys are preserved
# literally instead of being expanded by the heredoc.
E_SECRET_KEY="$(escape_env "$SECRET_KEY")"
E_LASTFM_API_KEY="$(escape_env "$LASTFM_API_KEY")"
E_LASTFM_API_SECRET="$(escape_env "$LASTFM_API_SECRET")"
E_DISCOGS_TOKEN="$(escape_env "$DISCOGS_TOKEN")"
E_YOUTUBE_API_KEY="$(escape_env "$YOUTUBE_API_KEY")"

cat > "$ENV_FILE" <<ENVFILE
# Generated by setup.sh — $(date +%Y-%m-%d)
# See env.example for all available options.

# ============================================================================
# REQUIRED
# ============================================================================

SECRET_KEY=${E_SECRET_KEY}

# ============================================================================
# SECURITY
# ============================================================================

FLASK_ENV=production
FLASK_DEBUG=0
ENABLE_TALISMAN=true
FORCE_HTTPS=false

# ============================================================================
# DATABASE
# ============================================================================

DATABASE_URL=sqlite:///data/nts.db

# ============================================================================
# INTEGRATIONS
# ============================================================================

# Last.fm — enriches artist metadata where available
LASTFM_API_KEY=${E_LASTFM_API_KEY}
LASTFM_API_SECRET=${E_LASTFM_API_SECRET}

# Discogs — vinyl release links on tracklist items
DISCOGS_TOKEN=${E_DISCOGS_TOKEN}
DISCOGS_USER_AGENT=NTSFeed/1.0

# YouTube — inline track playback
YOUTUBE_API_KEY=${E_YOUTUBE_API_KEY}
ENVFILE

ok "  .env written."
echo ""

# ---------------------------------------------------------------------------
# Scaffold data directories and seed files
# ---------------------------------------------------------------------------

bootstrap_output="$(bash ./scripts/bootstrap-runtime.sh)"
printf '%s\n' "$bootstrap_output" | while IFS= read -r line; do
    [ -n "$line" ] && dim "  $line"
done
echo ""

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

printf "${BOLD}${CYAN}  ── Summary ──${RESET}\n"
echo ""

status() {
    local label="$1" enabled="$2"
    if [ -n "$enabled" ]; then
        printf "  ${GREEN}%-14s enabled${RESET}\n" "$label"
    else
        printf "  ${DIM}%-14s  —${RESET}\n" "$label"
    fi
}

status "Last.fm"  "$LASTFM_API_KEY"
status "Discogs"  "$DISCOGS_TOKEN"
status "YouTube"  "$YOUTUBE_API_KEY"

echo ""

# ---------------------------------------------------------------------------
# Next steps
# ---------------------------------------------------------------------------

printf "${BOLD}${CYAN}  ── Next Steps ──${RESET}\n"
echo ""
echo "  1. Start the app:"
echo ""
printf "     ${BOLD}make quickstart${RESET}\n"
echo ""
echo "  2. Open https://localhost in your browser"
echo ""
if [ "$NONINTERACTIVE" -eq 1 ]; then
    dim "  Tip: setup.sh ran in non-interactive mode and used empty values for optional integrations."
fi
dim "  Tip: Run 'make docker-check' if Docker is installed but quickstart fails before build."
dim "  Tip: Run 'docker compose logs -f web nginx' to watch startup progress."
dim "  Tip: You can add or change API keys later by editing .env"
dim "       and running 'docker compose restart web'."
echo ""
