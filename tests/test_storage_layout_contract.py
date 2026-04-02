from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_docker_compose_uses_single_storage_root_mount() -> None:
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    compose_dev = (REPO_ROOT / "docker-compose.dev.yml").read_text(encoding="utf-8")

    assert "./storage:/app/storage" in compose
    assert "NTS_STORAGE_ROOT=/app/storage" in compose
    assert "./storage/thumbnails:/usr/share/nginx/html/thumbnails:ro" in compose
    assert "${AUTO_ADD_DIR_HOST:-./storage/auto_add_dir}:/app/storage/auto_add_dir" in compose
    assert "${MUSIC_DIR_HOST:-./storage/music_dir}:/app/storage/music_dir" in compose
    assert "${AUTO_ADD_DIR_HOST:-./storage/auto_add_dir}:/app/storage/auto_add_dir" in compose_dev
    assert "${MUSIC_DIR_HOST:-./storage/music_dir}:/app/storage/music_dir" in compose_dev


def test_env_example_uses_storage_root_defaults() -> None:
    env_example = (REPO_ROOT / "env.example").read_text(encoding="utf-8")

    assert "NTS_STORAGE_ROOT=storage" in env_example
    assert "DATABASE_URL=sqlite:///storage/data/nts.db" in env_example  # pragma: allowlist secret
    assert "IMAGE_CACHE_DIR=storage/thumbnails" in env_example
