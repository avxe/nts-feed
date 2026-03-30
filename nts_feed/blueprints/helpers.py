"""Shared helpers for blueprint routes.

Provides convenient accessors for services registered on ``current_app.extensions``
and common utility functions used across multiple blueprints.
"""

from datetime import datetime
from flask import current_app


# ---------------------------------------------------------------------------
# Service accessors
# ---------------------------------------------------------------------------

def get_ext(name, default=None):
    """Shorthand for ``current_app.extensions.get(name)``."""
    return current_app.extensions.get(name, default)


def get_db():
    return get_ext('db_sessionmaker')


def get_base():
    return get_ext('Base')

def get_track_manager():
    return get_ext('track_manager')


def get_image_cache():
    return get_ext('image_cache_service')


def get_lastfm():
    return get_ext('lastfm_service')


def get_discogs():
    return get_ext('discogs_service')


def get_youtube():
    return get_ext('youtube_service')


def get_update_service():
    return get_ext('update_service')


def db_available():
    """Return True if both Base and db_sessionmaker are available."""
    return bool(get_base() and get_db())


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def parse_episode_date(date_str):
    """Parse episode date strings like 'December 20, 2025' to datetime."""
    if not date_str:
        return None
    for fmt in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(date_str.replace('Z', '+00:00'))
    except ValueError:
        pass
    return None
