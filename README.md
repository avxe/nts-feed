# NTS Feed

The missing feed for your fav NTS shows. NTS Feed is a self-hosted app for keeping up with [NTS Radio](https://www.nts.live/). Subscribe to your favorite DJs, download episodes, play tracks via Youtube, and make your own playlists.

## Why use this?

NTS is great, but there's no built-in unified way to follow your fav DJs, save episodes offline, 

- **Subscribe to shows** — paste any NTS show URL and the app tracks new episodes for you.
- **Download episodes** — save audio locally with proper metadata and artwork.
- **Discover** — get personalized recommendations based on your listening history, or browse through genres.
- **Search everything** — find shows, episodes, tracks, artists, and genres in one search bar.
- **Build a library** — like tracks, save episodes, create playlists.
- **Optional extras** — connect Discogs, Last.fm, or YouTube for richer track info and inline playback.

## Quick start (Docker)

You'll need [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running. That's it.

```bash
git clone https://github.com/avxe/nts-feed.git
cd nts-feed
make quickstart
```

`make quickstart` walks you through creating a `.env` file if one is missing, reuses your existing `.env` if it already exists, checks Docker/Compose/buildx, and then builds and starts everything.

Open [https://localhost](https://localhost) when it's ready. You'll see a self-signed certificate warning — that's normal for local HTTPS, just click through it.

If Docker is installed but `make quickstart` stops before build, run:

```bash
make docker-check
```

To watch what's happening:

```bash
make docker-logs
```

To stop:

```bash
make docker-down
```

## Quick start (without Docker)

If you'd rather run it directly with Python 3.11+:

```bash
git clone https://github.com/avxe/nts-feed.git
cd nts-feed
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
cp env.example .env
```

Open `.env` and set a `SECRET_KEY` (there's a one-liner in the file to generate one). Then:

```bash
python run.py
```

Open [http://localhost:5555](http://localhost:5555).

## Configuration

The only required setting is `SECRET_KEY`. Everything else is optional.


| Variable          | What it does                                                                                                |
| ----------------- | ----------------------------------------------------------------------------------------------------------- |
| `SECRET_KEY`      | **Required.** Session signing key. Generate with `python -c "import secrets; print(secrets.token_hex(32))"` |
| `LASTFM_API_KEY`  | Enriches artist and track metadata                                                                          |
| `DISCOGS_TOKEN`   | Adds release links to tracklist items                                                                       |
| `YOUTUBE_API_KEY` | Enables inline YouTube playback for tracks                                                                  |


See `env.example` for the full list.

## What's in the codebase

This is a **Python/Flask** web app with server-rendered HTML templates and vanilla JavaScript on the frontend. No frameworks, no build step.

```
nts-feed/
├── nts_feed/        # Flask app — blueprints, services, database, CLI tools
├── templates/          # Jinja2 HTML templates
├── static/             # CSS and JavaScript (no build step needed)
├── data/               # SQLite database (created on first run)
├── episodes/           # Episode metadata (JSON)
├── downloads/          # Downloaded audio files
├── thumbnails/         # Cached artwork
├── shows.json          # Your show subscriptions
├── docker-compose.yml  # Production Docker setup
├── Makefile            # Common commands (run `make help` to see them all)
└── docs/               # Architecture docs, API reference, etc.
```

Key pieces under `nts_feed/`:

- `**blueprints/**` — Route handlers for pages, search, downloads, API endpoints
- `**services/**` — Discogs, Last.fm, YouTube, image caching, recommendations
- `**db/**` — SQLAlchemy models and SQLite management
- `**cli/**` — Maintenance commands (`build-database`, `backfill-tracklists`, etc.)

## Data storage

Everything lives in local files and a SQLite database — no external database server needed.


| Path          | What's in it                                                        |
| ------------- | ------------------------------------------------------------------- |
| `shows.json`  | Your subscribed shows                                               |
| `episodes/`   | Episode metadata per show                                           |
| `downloads/`  | Saved audio files                                                   |
| `thumbnails/` | Cached show artwork                                                 |
| `data/nts.db` | SQLite database (search index, likes, playlists, listening history) |


## Common issues


| Problem                               | Fix                                                                                         |
| ------------------------------------- | ------------------------------------------------------------------------------------------- |
| "Cannot connect to the Docker daemon" | Start Docker Desktop first                                                                  |
| Port 80 or 443 already in use         | Stop whatever else is using those ports, or change the port mapping in `docker-compose.yml` |
| Browser HTTPS warning                 | Expected for local self-signed certs — click through it                                     |
| First load is slow                    | The database is building for the first time. Check `make docker-logs`                       |


## Useful commands

```bash
make help          # see all available commands
make docker-dev    # start in dev mode with hot reload
make test          # run tests
make lint          # check code style
```

## Docs

- [Architecture](docs/architecture.md) — how the app is structured
- [Discover](docs/DISCOVER.md) — how episode recommendations work
- [Installation](docs/installation.md) — detailed setup guide

## Contributing

Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) before opening a PR.

## License

[MIT](https://opensource.org/licenses/MIT)
