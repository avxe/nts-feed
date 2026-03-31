# Architecture

This document describes the supported runtime shape of NTS Feed.

## Runtime Model

NTS Feed is a Flask application for managing a personal NTS episode library.

- JSON files remain the source of truth for subscribed shows and episode metadata.
- SQLite is the query store for search, discover, likes, playlists, and listening history.
- The web app runs as a single logical application with background jobs for updates, downloads, and maintenance tasks.
- Optional integrations enrich the experience, but the core product works without them.

## Main Components

```text
Browser
  |
  v
nginx
  |
  v
Flask application
  |
  +-- Runtime storage (storage/shows.json, storage/episodes/*.json)
  +-- SQLite database (storage/data/nts.db)
  +-- Background jobs (updates, downloads, maintenance)
  +-- Optional external services (Discogs, Last.fm, YouTube, media hosts)
```

## Data Ownership

### Source of truth

- `storage/shows.json`: subscribed show metadata.
- `storage/episodes/<slug>.json`: episode metadata per show.

### Derived/query data

SQLite stores normalized entities used by the UI and maintenance flows:

- shows
- episodes
- tracks
- artists
- genres
- liked tracks
- liked episodes
- user playlists
- listening sessions
- discover inbox state
- saved mixtapes

## Request Shapes

### Pages

Server-rendered pages deliver the initial HTML shell and persistent UI chrome.

### JSON endpoints

API routes power discover shelves, search, likes, playlists, maintenance jobs, and track metadata lookups.

### Progress streams

Long-running operations such as updates, subscriptions, and downloads expose SSE progress streams.

## Discover Model

Discover is episode-first and SQL-backed.

- `Next Up` is the action-oriented listening inbox.
- `Explore` is the broader recommendation surface.
- Ranking is deterministic and derived from subscriptions, likes, listening history, and inbox state.
- The supported runtime does not depend on vector search, embeddings, or Qdrant.

## Extension Strategy

The project is intended to grow through explicit seams rather than monolithic route files.

- Add HTTP behavior through focused blueprints.
- Add reusable logic through services with app-scoped registration.
- Add maintenance flows through packaged CLIs or background-job helpers.
- Add UI behavior through page modules and documented SPA lifecycle hooks.

See `docs/extension-points.md` and `docs/frontend-architecture.md` for contributor-facing details.
