# Changelog

All notable changes to NTS Feed are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Modernized repository packaging with `pyproject.toml` and a documented dev extra.
- Tightened CI to run Python tests, JS tests, packaging smoke checks, and secret scanning.
- Removed stale repo artifacts, unsupported deployment leftovers, and outdated documentation.

### Security
- Removed tracked secret material from the repository surface and tightened ignore rules.
- Added repository-level secret scanning expectations for local hooks and CI.

## [0.1.0] - 2026-02-09

### Added
- Show subscriptions with tracked episode metadata stored locally in JSON.
- Episode downloading with metadata tagging, artwork caching, and download progress reporting.
- Discover workflows built around `Next Up` and `Explore`.
- Track likes, episode likes, and user playlists.
- SQL-backed search across shows, episodes, tracks, artists, and genres.
- Inline YouTube playback for track previews.
- Docker-based local deployment with nginx in front of the Flask app.

### Security
- Non-root Docker container user.
- Content Security Policy and Flask-Talisman integration.
- HTTPS-by-default local reverse proxy path through nginx.
- Gunicorn worker recycling and hardened response headers.
