"""Runtime storage paths and migration helpers."""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from .storage.paths import (
    get_auto_add_dir,
    get_downloaded_tracks_path,
    get_genre_taxonomy_path,
    get_image_cache_dir,
    get_music_dir,
    get_project_root,
    get_settings_path,
    get_shows_backup_path,
    get_shows_path,
    get_storage_cache_dir,
    get_storage_data_dir,
    get_storage_path,
    get_storage_root,
    get_thumbnails_dir,
    get_youtube_cache_dir,
)


def project_root() -> Path:
    return get_project_root()


def storage_root() -> Path:
    return get_storage_root()


def storage_path(*parts: str) -> Path:
    return get_storage_path(*parts)


def data_path(*parts: str) -> Path:
    return get_storage_data_dir().joinpath(*parts)


def cache_path(*parts: str) -> Path:
    return get_storage_cache_dir().joinpath(*parts)


def episodes_dir() -> Path:
    return storage_path("episodes")


def downloads_dir() -> Path:
    return storage_path("downloads")


def thumbnails_dir() -> Path:
    return get_thumbnails_dir()


def shows_path() -> Path:
    return get_shows_path()


def shows_backup_path() -> Path:
    return get_shows_backup_path()


def music_dir() -> Path:
    return get_music_dir()


def auto_add_dir() -> Path:
    return get_auto_add_dir()


def image_cache_dir() -> Path:
    return get_image_cache_dir()


def database_file_path() -> Path:
    return data_path("nts.db")


def settings_file_path() -> Path:
    return get_settings_path()


def youtube_cache_dir() -> Path:
    return get_youtube_cache_dir()


def genre_taxonomy_cache_path() -> Path:
    return get_genre_taxonomy_path()


def downloaded_tracks_path() -> Path:
    return get_downloaded_tracks_path()


def ensure_runtime_layout() -> dict[str, list[str]]:
    """Ensure the consolidated storage tree exists and migrate legacy paths."""
    root = project_root()
    storage = storage_root()
    storage.mkdir(parents=True, exist_ok=True)

    moved: list[str] = []
    created: list[str] = []
    backed_up: list[str] = []

    legacy_shows_dir = root / "shows.json"
    if legacy_shows_dir.is_dir():
        backup_name = root / f"shows.json.dir-backup.{datetime.now():%Y%m%d%H%M%S}"
        shutil.move(str(legacy_shows_dir), str(backup_name))
        backed_up.append(str(backup_name))

    legacy_map = {
        root / "shows.json": shows_path(),
        root / "shows.json.backup": shows_backup_path(),
        root / "episodes": episodes_dir(),
        root / "downloads": downloads_dir(),
        root / "thumbnails": thumbnails_dir(),
        root / "data": data_path(),
        root / "cache": cache_path(),
        root / "auto_add_dir": auto_add_dir(),
        root / "music_dir": music_dir(),
    }

    for legacy_path, target_path in legacy_map.items():
        if not legacy_path.exists() or target_path.exists():
            continue
        target_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(legacy_path), str(target_path))
        moved.append(f"{legacy_path} -> {target_path}")

    for path in (
        episodes_dir(),
        downloads_dir(),
        thumbnails_dir(),
        data_path(),
        cache_path(),
        auto_add_dir(),
        music_dir(),
        youtube_cache_dir(),
    ):
        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            created.append(str(path))

    if not shows_path().exists():
        shows_path().write_text("{}\n", encoding="utf-8")
        created.append(str(shows_path()))

    if not shows_path().is_file():
        raise RuntimeError(f"Expected {shows_path()} to be a file")

    return {
        "moved": moved,
        "created": created,
        "backed_up": backed_up,
    }
