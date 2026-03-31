from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_SCRIPT = REPO_ROOT / "scripts" / "bootstrap-runtime.sh"
CHECK_ENV_SCRIPT = REPO_ROOT / "scripts" / "check-env.sh"


def _make_root(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "config" / "nginx" / "ssl").mkdir(parents=True)
    return root


def _fake_openssl(tmp_path: Path) -> Path:
    openssl_path = tmp_path / "fake-openssl"
    openssl_path.write_text(
        """#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in
    -keyout)
      shift
      keyout="$1"
      ;;
    -out)
      shift
      certout="$1"
      ;;
  esac
  shift
done
printf 'key' > "$keyout"
printf 'cert' > "$certout"
""",
        encoding="utf-8",
    )
    openssl_path.chmod(openssl_path.stat().st_mode | stat.S_IEXEC)
    return openssl_path


def test_check_env_rejects_placeholder_secret(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / ".env").write_text("SECRET_KEY=your_secret_key_here\n", encoding="utf-8")

    result = subprocess.run(
        ["bash", str(CHECK_ENV_SCRIPT)],
        capture_output=True,
        text=True,
        env={**os.environ, "NTS_FEED_ROOT": str(root)},
        check=False,
    )

    assert result.returncode == 1
    assert "SECRET_KEY is missing or still set to a placeholder value" in result.stderr


def test_check_env_rejects_placeholder_docker_mount_paths(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    (root / ".env").write_text(
        "\n".join(
            [
                "SECRET_KEY=test-secret-key",
                "AUTO_ADD_DIR_HOST=/absolute/path/to/Automatically Add to Music.localized",
                "MUSIC_DIR_HOST=/absolute/path/to/Music/Compilations/NTS",  # pragma: allowlist secret
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        ["bash", str(CHECK_ENV_SCRIPT), "--for-docker"],
        capture_output=True,
        text=True,
        env={**os.environ, "NTS_FEED_ROOT": str(root)},
        check=False,
    )

    assert result.returncode == 1
    assert "AUTO_ADD_DIR_HOST still points at the sample placeholder" in result.stderr
    assert "MUSIC_DIR_HOST still points at the sample placeholder" in result.stderr


def test_bootstrap_runtime_repairs_shows_json_directory_and_scaffolds(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    broken_shows_dir = root / "shows.json"
    broken_shows_dir.mkdir()
    (broken_shows_dir / "orphaned.txt").write_text("data", encoding="utf-8")
    (root / "episodes").mkdir()
    (root / "episodes" / "legacy-show.json").write_text('{"episodes": []}\n', encoding="utf-8")
    (root / "downloads").mkdir()
    (root / "downloads" / "legacy.m4a").write_text("audio", encoding="utf-8")
    (root / "thumbnails").mkdir()
    (root / "thumbnails" / "cover.jpg").write_text("image", encoding="utf-8")
    (root / "data").mkdir()
    (root / "data" / "nts.db").write_text("db", encoding="utf-8")
    (root / "auto_add_dir").mkdir()
    (root / "music_dir").mkdir()
    fake_openssl = _fake_openssl(tmp_path)

    result = subprocess.run(
        ["bash", str(BOOTSTRAP_SCRIPT)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NTS_FEED_ROOT": str(root),
            "OPENSSL_BIN": str(fake_openssl),
        },
        check=False,
    )

    assert result.returncode == 0
    assert not (root / "shows.json").exists()
    assert not (root / "episodes").exists()
    assert not (root / "downloads").exists()
    assert not (root / "thumbnails").exists()
    assert not (root / "data").exists()
    assert not (root / "auto_add_dir").exists()
    assert not (root / "music_dir").exists()
    assert (root / "storage" / "shows.json").is_file()
    assert (root / "storage" / "episodes").is_dir()
    assert (root / "storage" / "downloads").is_dir()
    assert (root / "storage" / "thumbnails").is_dir()
    assert (root / "storage" / "data").is_dir()
    assert (root / "storage" / "auto_add_dir").is_dir()
    assert (root / "storage" / "music_dir").is_dir()
    assert list(root.glob("shows.json.dir-backup.*"))
    assert (root / "storage" / "episodes" / "legacy-show.json").is_file()
    assert (root / "storage" / "downloads" / "legacy.m4a").is_file()
    assert (root / "storage" / "thumbnails" / "cover.jpg").is_file()
    assert (root / "storage" / "data" / "nts.db").is_file()
    assert (root / "config" / "nginx" / "ssl" / "fullchain.pem").is_file()
    assert (root / "config" / "nginx" / "ssl" / "privkey.pem").is_file()


def test_bootstrap_runtime_skips_certs_when_openssl_missing(tmp_path: Path) -> None:
    root = _make_root(tmp_path)
    missing_binary = tmp_path / "missing-openssl"
    if missing_binary.exists():
        if missing_binary.is_dir():
            shutil.rmtree(missing_binary)
        else:
            missing_binary.unlink()

    result = subprocess.run(
        ["bash", str(BOOTSTRAP_SCRIPT)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "NTS_FEED_ROOT": str(root),
            "OPENSSL_BIN": str(missing_binary),
        },
        check=False,
    )

    assert result.returncode == 0
    assert "OpenSSL not found. Skipping local certificate generation" in result.stdout
    assert not (root / "config" / "nginx" / "ssl" / "fullchain.pem").exists()
    assert not (root / "config" / "nginx" / "ssl" / "privkey.pem").exists()
