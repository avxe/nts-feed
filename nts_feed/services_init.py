"""Service initialization and registration on the Flask app."""

from __future__ import annotations

import os

from .db.bootstrap import _ensure_episode_inbox_state_schema
from .runtime_paths import image_cache_dir, music_dir


def init_services(app):
    """Initialize non-database services and register them on ``app.extensions``."""
    from .services.discogs_service import DiscogsService
    from .services.image_cache_service import ImageCacheService
    from .services.lastfm_service import LastFmService
    from .services.update_service import UpdateService
    from .services.youtube_service import YouTubeService
    from .track_manager import TrackManager

    track_manager = TrackManager(music_dir=str(music_dir()))
    if not track_manager.downloaded_tracks.get('episodes'):
        track_manager.scan_directory()
    app.extensions['track_manager'] = track_manager

    image_cache_service = ImageCacheService(
        cache_dir=str(image_cache_dir()),
    )
    app.extensions['image_cache_service'] = image_cache_service

    discogs_service = DiscogsService()
    if hasattr(discogs_service, 'token'):
        discogs_service.token = os.getenv('DISCOGS_TOKEN', '')
    if hasattr(discogs_service, 'user_agent'):
        discogs_service.user_agent = 'NTSFeed/1.0'
    app.extensions['discogs_service'] = discogs_service

    app.extensions['youtube_service'] = YouTubeService(api_key=os.getenv('YOUTUBE_API_KEY', ''))
    app.extensions['lastfm_service'] = LastFmService()
    app.extensions['update_service'] = app.extensions.get('update_service') or UpdateService()

    app.extensions['progress_queues'] = app.extensions.get('progress_queues', {})
    app.extensions['active_downloads'] = app.extensions.get('active_downloads', {})
    if not hasattr(app, 'thumbnail_warm_jobs'):
        app.thumbnail_warm_jobs = {}

    return app


__all__ = ['init_services', '_ensure_episode_inbox_state_schema']
