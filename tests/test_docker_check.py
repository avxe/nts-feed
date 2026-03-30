from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check-docker.sh"


def _write_fake_docker(tmp_path: Path) -> Path:
    docker_path = tmp_path / "docker"
    docker_path.write_text(
        """#!/bin/sh
case "$1:$2" in
  "compose:version")
    [ "${FAKE_DOCKER_COMPOSE:-ok}" = "ok" ] && exit 0 || exit 1
    ;;
  "buildx:version")
    [ "${FAKE_DOCKER_BUILDX:-ok}" = "ok" ] && exit 0 || exit 1
    ;;
  "info:")
    [ "${FAKE_DOCKER_INFO:-ok}" = "ok" ] && exit 0 || exit 1
    ;;
  *)
    exit 0
    ;;
esac
""",
        encoding="utf-8",
    )
    docker_path.chmod(docker_path.stat().st_mode | stat.S_IEXEC)
    return docker_path


def test_docker_check_reports_all_missing_prereqs(tmp_path: Path) -> None:
    _write_fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_DOCKER_COMPOSE"] = "ok"
    env["FAKE_DOCKER_BUILDX"] = "missing"
    env["FAKE_DOCKER_INFO"] = "missing"

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 1
    assert "Docker buildx plugin is not available" in result.stderr
    assert "Docker daemon is not running" in result.stderr
    assert "rerun `make quickstart`" in result.stderr


def test_docker_check_succeeds_when_prereqs_are_present(tmp_path: Path) -> None:
    _write_fake_docker(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_DOCKER_COMPOSE"] = "ok"
    env["FAKE_DOCKER_BUILDX"] = "ok"
    env["FAKE_DOCKER_INFO"] = "ok"

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Docker prerequisites look good." in result.stdout
