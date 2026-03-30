# Installation Guide

This document covers the supported ways to run NTS Feed locally.

## Requirements

- Python 3.11 or 3.12 for source installs
- `ffmpeg`
- Docker with Compose support for containerized installs

## Recommended: Docker

```bash
git clone https://github.com/avxe/nts-feed.git
cd nts-feed
make quickstart
```

Open [https://localhost](https://localhost).

`make quickstart` creates `.env` when needed, reuses an existing `.env` without prompting again, checks Docker prerequisites, builds the image, and starts the tracked dev stack.

If you want to manage setup and container startup separately:

```bash
make setup
make docker-build
make docker-up
```

### Verify startup

```bash
make docker-check
docker compose ps
docker compose logs -f web nginx
```

### Manual smoke test

1. Open `/`.
2. Add a show from an NTS URL.
3. Open `/discover` and confirm `Next Up` or `Explore` renders.
4. Open `/search?q=house` and confirm grouped results render.
5. Open `/likes` and confirm likes/playlists load.
6. Download one episode and confirm progress updates and file output work.
7. Open `/admin` and confirm stats/settings load.

## From Source

```bash
git clone https://github.com/avxe/nts-feed.git
cd nts-feed
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e .[dev]
cp env.example .env
python run.py
```

Open [http://localhost:5555](http://localhost:5555).

## Configuration

The only required setting is:

```bash
SECRET_KEY=your_secret_key_here
```

Optional integrations:

```bash
LASTFM_API_KEY=
LASTFM_API_SECRET=
DISCOGS_TOKEN=
YOUTUBE_API_KEY=
```

Host-path overrides for Docker bind mounts:

```bash
AUTO_ADD_DIR_HOST=/absolute/path/to/Automatically\ Add\ to\ Music.localized
MUSIC_DIR_HOST=/absolute/path/to/Music/Compilations/NTS
```

## Useful Checks

```bash
build-database --help
backfill-tracklists --help
backfill-auto-downloads --help
recover-shows --help
ruff check .
.venv/bin/pytest tests/ -q
node --test tests/js/*.mjs
python -m build --sdist --wheel
```

## Troubleshooting

- `Cannot connect to the Docker daemon`: start Docker, wait for it to finish booting, then rerun `make docker-check`.
- `Docker Compose requires buildx plugin to be installed`: update Docker Desktop or install the Docker buildx plugin, then rerun `make docker-check`.
- Port `80` or `443` already in use: change nginx host port mappings.
- HTTPS warning in browser: expected for the local self-signed certificate.
- First load is slow: the app may be rebuilding the SQLite query store or warming assets.
- `shows.json` errors on startup: rerun `make setup` so the bind-mounted file exists as a file, not a directory.
