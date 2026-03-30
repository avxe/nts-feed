from __future__ import annotations

import os
import socket
import stat
import subprocess
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check-docker.sh"


def _write_fake_docker(tmp_path: Path) -> Path:
    docker_path = tmp_path / "docker"
    docker_path.write_text(
        """#!/bin/sh
if [ "$1" = "compose" ] && [ "$2" = "version" ]; then
  [ "${FAKE_DOCKER_COMPOSE:-ok}" = "ok" ] && exit 0 || exit 1
fi

if [ "$1" = "buildx" ] && [ "$2" = "version" ]; then
  [ "${FAKE_DOCKER_BUILDX:-ok}" = "ok" ] && exit 0 || exit 1
fi

if [ "$1" = "info" ]; then
  [ "${FAKE_DOCKER_INFO:-ok}" = "ok" ] && exit 0 || exit 1
fi

if printf '%s ' "$@" | grep -q "ps --services --status running"; then
  [ "${FAKE_DOCKER_NGINX_RUNNING:-0}" = "1" ] && printf 'nginx\n'
  exit 0
fi

exit 0
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
    env["NTS_FEED_REQUIRED_PORTS"] = ""

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
    env["NTS_FEED_REQUIRED_PORTS"] = ""

    result = subprocess.run(
        ["bash", str(SCRIPT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Docker prerequisites look good." in result.stdout


def test_docker_check_reports_busy_port_when_other_process_owns_it(tmp_path: Path) -> None:
    _write_fake_docker(tmp_path)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_DOCKER_COMPOSE"] = "ok"
    env["FAKE_DOCKER_BUILDX"] = "ok"
    env["FAKE_DOCKER_INFO"] = "ok"
    env["NTS_FEED_REQUIRED_PORTS"] = str(port)

    try:
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        sock.close()

    assert result.returncode == 1
    assert f"Port {port} is already in use on this machine" in result.stderr


def test_docker_check_allows_busy_port_for_running_nts_feed_stack(tmp_path: Path) -> None:
    _write_fake_docker(tmp_path)
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen()
    port = sock.getsockname()[1]
    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FAKE_DOCKER_COMPOSE"] = "ok"
    env["FAKE_DOCKER_BUILDX"] = "ok"
    env["FAKE_DOCKER_INFO"] = "ok"
    env["FAKE_DOCKER_NGINX_RUNNING"] = "1"
    env["NTS_FEED_REQUIRED_PORTS"] = str(port)

    try:
        result = subprocess.run(
            ["bash", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
    finally:
        sock.close()

    assert result.returncode == 0
    assert f"Port {port} is already in use by the running NTS Feed stack" in result.stdout
