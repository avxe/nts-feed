"""Canonical runtime storage path helpers."""

from __future__ import annotations

import os
from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = PACKAGE_ROOT.parent


def get_project_root() -> Path:
    custom_root = os.getenv("NTS_FEED_ROOT")
    if not custom_root:
        return PROJECT_ROOT
    return Path(custom_root).expanduser().resolve()


def get_storage_root() -> Path:
    custom_root = os.getenv("NTS_STORAGE_ROOT")
    if custom_root:
        return _resolve_path(custom_root)
    return (get_project_root() / "storage").resolve()


def get_storage_path(*parts: str) -> Path:
    return get_storage_root().joinpath(*parts)


def get_shows_path() -> Path:
    return get_storage_path("shows.json")


def get_shows_backup_path() -> Path:
    return get_storage_path("shows.json.backup")


def get_episodes_dir() -> Path:
    return get_storage_path("episodes")


def get_downloads_dir() -> Path:
    return get_storage_path("downloads")


def get_thumbnails_dir() -> Path:
    return get_storage_path("thumbnails")


def get_storage_data_dir() -> Path:
    return get_storage_path("data")


def get_storage_cache_dir() -> Path:
    return get_storage_path("cache")


def get_image_cache_dir() -> Path:
    return _get_runtime_dir_override("IMAGE_CACHE_DIR", ("thumbnails",), {"thumbnails"})


def get_music_dir() -> Path:
    return _get_runtime_dir_override("MUSIC_DIR", ("music_dir",), {"music_dir", "/app/music_dir"})


def get_auto_add_dir() -> Path:
    return _get_runtime_dir_override("AUTO_ADD_DIR", ("auto_add_dir",), {"auto_add_dir", "/app/auto_add_dir"})


def get_settings_path() -> Path:
    custom_path = os.getenv("NTS_SETTINGS_PATH")
    if custom_path:
        return _resolve_path(custom_path)
    return get_storage_data_dir() / "settings.json"


def get_downloaded_tracks_path() -> Path:
    return get_storage_data_dir() / "downloaded_tracks.json"


def get_genre_taxonomy_path() -> Path:
    return get_storage_data_dir() / "genre_taxonomy.json"


def get_youtube_cache_dir() -> Path:
    return get_storage_data_dir() / "youtube_cache"


def get_rebuild_lock_path() -> Path:
    return get_storage_data_dir() / "db-rebuild.lock"


def normalize_database_url(raw_url: str | None) -> str | None:
    if raw_url in (None, ""):
        return None
    if raw_url == "sqlite:///data/nts.db":
        return f"sqlite:///{(get_storage_data_dir() / 'nts.db').resolve()}"
    return raw_url


def resolve_database_url(raw_url: str | None = None) -> str:
    normalized = normalize_database_url(raw_url or os.getenv("DATABASE_URL"))
    if normalized:
        return normalized
    return f"sqlite:///{(get_storage_data_dir() / 'nts.db').resolve()}"


def _get_runtime_dir_override(env_name: str, default_parts: tuple[str, ...], legacy_values: set[str]) -> Path:
    raw_value = os.getenv(env_name)
    if raw_value in (None, ""):
        return get_storage_path(*default_parts)
    normalized = raw_value.strip()
    if normalized in legacy_values:
        return get_storage_path(*default_parts)
    return _resolve_path(normalized)


def _resolve_path(raw_path: str | os.PathLike[str]) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return get_project_root() / path


__all__ = [
    "get_auto_add_dir",
    "get_downloaded_tracks_path",
    "get_downloads_dir",
    "get_episodes_dir",
    "get_genre_taxonomy_path",
    "get_image_cache_dir",
    "get_music_dir",
    "get_project_root",
    "get_rebuild_lock_path",
    "get_settings_path",
    "get_shows_backup_path",
    "get_shows_path",
    "get_storage_cache_dir",
    "get_storage_data_dir",
    "get_storage_path",
    "get_storage_root",
    "get_thumbnails_dir",
    "get_youtube_cache_dir",
    "normalize_database_url",
    "resolve_database_url",
]
