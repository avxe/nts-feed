from __future__ import annotations

import json
from pathlib import Path

from nts_feed.runtime_paths import ensure_runtime_layout, storage_path


def test_ensure_runtime_layout_migrates_legacy_runtime_state(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NTS_FEED_ROOT", str(tmp_path))

    (tmp_path / "episodes").mkdir()
    (tmp_path / "downloads").mkdir()
    (tmp_path / "shows.json").write_text(json.dumps({"show": {"title": "Test Show"}}), encoding="utf-8")
    (tmp_path / "episodes" / "test-show.json").write_text(
        json.dumps({"episodes": [{"title": "Episode One"}]}),
        encoding="utf-8",
    )
    (tmp_path / "downloads" / "episode.m4a").write_text("audio", encoding="utf-8")

    ensure_runtime_layout()

    assert not (tmp_path / "shows.json").exists()
    assert not (tmp_path / "episodes").exists()
    assert not (tmp_path / "downloads").exists()
    assert storage_path("shows.json").read_text(encoding="utf-8")
    assert (storage_path("episodes") / "test-show.json").is_file()
    assert (storage_path("downloads") / "episode.m4a").is_file()


def test_ensure_runtime_layout_creates_default_storage_tree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NTS_FEED_ROOT", str(tmp_path))

    ensure_runtime_layout()

    assert storage_path("shows.json").is_file()
    assert storage_path("episodes").is_dir()
    assert storage_path("downloads").is_dir()
    assert storage_path("thumbnails").is_dir()
    assert storage_path("data").is_dir()
    assert storage_path("cache").is_dir()
    assert storage_path("auto_add_dir").is_dir()
    assert storage_path("music_dir").is_dir()
