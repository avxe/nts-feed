# API Reference

NTS Feed exposes a small set of JSON and streaming endpoints for search, discover, downloads, collection management, and maintenance tasks.

## Conventions

- Unless noted otherwise, responses are JSON.
- Authentication is not currently built into the app.
- Admin settings endpoints are localhost-only by default.
- Some long-running operations return a job ID or stream progress over Server-Sent Events.

## Discover

- `GET /api/discover`: returns the current Explore shelves and listening summary.
- `POST /api/discover/surprise`: returns one surprise episode card from the current candidate pool.
- `GET /api/discover/genre/<genre_slug>`: returns the shelf for one discover genre.
- `GET /api/discover/next-up`: returns the action-oriented Next Up sections.
- `POST /api/discover/next-up/state`: mutates Next Up inbox state for an episode.

## Likes and Playlists

- `GET /api/likes`
- `POST /api/likes`
- `DELETE /api/likes/<like_id>`
- `POST /api/likes/check`
- `GET /api/episodes/likes`
- `POST /api/episodes/likes`
- `DELETE /api/episodes/likes/<like_id>`
- `POST /api/episodes/likes/check`
- `GET /api/user_playlists`
- `POST /api/user_playlists`
- `GET /api/user_playlists/<playlist_id>`
- `PUT /api/user_playlists/<playlist_id>`
- `DELETE /api/user_playlists/<playlist_id>`
- `POST /api/user_playlists/<playlist_id>/tracks`
- `DELETE /api/user_playlists/<playlist_id>/tracks/<playlist_track_id>`
- `POST /api/user_playlists/<playlist_id>/reorder`

## Search and Track Data

- `GET /api/search`
- `GET /api/tracks/search`
- `GET /api/episodes/search`
- `GET /api/shows/search`
- `GET /api/tracks`
- `POST /api/tracks/by_ids`
- `POST /api/episodes/by_ids`
- `POST /api/shows/by_ids`
- `GET /api/show/<path:url>/episodes`
- `GET /api/show/<path:url>/episode`
- `GET /api/track/<track_id>/episodes`
- `GET /api/track/<track_id>/explain`
- `GET /api/episode_tracklist`
- `GET /api/episode_audio/<path:episode_url>`
- `GET /api/artist_info`
- `GET /api/track_info`
- `GET /api/genres`
- `GET /api/genres/explore`
- `POST /api/update_track`

## Integrations

- `POST /download_track`: resolves a Discogs release link for a track.
- `POST /search_youtube`: resolves or falls back to a YouTube result for a track.
- `GET /api/lastfm/artist_info`
- `GET /api/lastfm/track_info`
- `GET /api/lastfm/similar_artists`

## Collection Management

- `POST /subscribe_async`
- `GET /subscribe_progress/<subscribe_id>`: SSE progress stream for subscription/import work.
- `POST /update`
- `POST /update_show/<path:url>`
- `POST /update_async`
- `GET /update_progress/<update_id>`: SSE progress stream for whole-library updates.
- `POST /toggle_auto_download/<path:show_id>`
- `POST /mark_read/<path:url>`
- `POST /delete/<path:url>`

## Downloads

- `GET /download_episode/<path:url>`
- `GET /download_all/<path:url>`
- `POST /cancel_download/<batch_id>`
- `GET /progress/<download_id>`: SSE progress stream for downloads.
- `GET /check_active_downloads`

## Database and Maintenance

- `POST /api/db/rebuild`
- `GET /api/db/rebuild/<job_id>`
- `POST /api/db/incremental`
- `POST /api/sync`
- `GET /api/sync/<job_id>`
- `POST /api/episodes/deduplicate`
- `POST /api/backfill_tracklists`
- `POST /api/taxonomy/build`
- `GET /api/taxonomy/build/status`
- `GET /api/taxonomy/status`
- `GET /api/taxonomy/lookup/<genre>`
- `POST /admin/warm_thumbnails`
- `GET /admin/warm_thumbnails/<warm_id>`

## Admin

- `GET /api/admin/stats`
- `POST /api/admin/recalculate-track-weights`
- `GET /api/admin/recalculate-track-weights/progress`
- `GET /api/admin/settings`
- `PUT /api/admin/settings`

## Health and Cached Assets

- `GET /health`
- `GET /thumbnail`

## Stability Notes

- `/discover` is the primary product surface; `/mixtape` remains a page alias for compatibility.
- The old vector-search and Qdrant-era endpoints are not part of the supported runtime.
- When adding endpoints, update this file and the route-contract tests together.
