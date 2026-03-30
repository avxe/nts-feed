# Extension Points

This document describes the supported seams for extending NTS Feed without growing the codebase into a monolith.

## Backend

### Blueprints

Add new HTTP behavior through focused blueprints grouped by user-facing domain, not by arbitrary helpers.

Good examples:

- discover
- likes and playlists
- search and track metadata
- admin and maintenance

### Services

Reusable logic should live behind services registered on the Flask app.

Use services for:

- external APIs
- caching
- media resolution
- ranking or recommendation logic
- maintenance operations

Do not instantiate shared clients ad hoc in random route handlers.

### Background Jobs

Long-running work should expose one of these patterns:

- background thread or executor job plus JSON job status
- SSE progress stream
- packaged CLI for offline or operator-triggered maintenance

### Storage

- JSON is the source of truth for subscriptions and raw episode metadata.
- SQLite is the query store for UI-facing relational access patterns.
- New storage layers should be justified explicitly and documented before adoption.

## Frontend

### Global infrastructure

Add to the global shell only when the feature truly persists across pages.

### Page modules

Prefer page-owned modules with explicit `init()` and `cleanup()` behavior.

### DOM contracts

If JavaScript depends on selectors, IDs, or data attributes, treat them as part of the supported internal contract and test them.

## Maintenance Commands

Supported operator workflows should be shipped as documented CLIs, not hidden scripts.

Current commands:

- `build-database`
- `backfill-tracklists`
- `backfill-auto-downloads`
- `recover-shows`

If a maintenance script is not documented and tested, it is not part of the supported extension surface.

## Documentation Requirements

When adding a new extension seam:

- update the relevant architecture doc
- document the public or internal contract
- add tests that prove the seam still works
- describe verification steps in the PR
