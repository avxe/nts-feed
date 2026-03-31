from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = REPO_ROOT / "docker-entrypoint.sh"


def _write_fake_executable(path: Path, script: str) -> None:
    path.write_text(script, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def test_entrypoint_uses_gunicorn_when_debug_is_off(tmp_path: Path) -> None:
    called = tmp_path / "called.txt"
    _write_fake_executable(
        tmp_path / "gunicorn",
        f"""#!/bin/sh
printf 'gunicorn %s\\n' "$*" > "{called}"
exit 0
""",
    )
    _write_fake_executable(
        tmp_path / "python",
        f"""#!/bin/sh
printf 'python %s\\n' "$*" > "{called}"
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FLASK_DEBUG"] = "0"
    env["FLASK_ENV"] = "production"

    result = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Starting Gunicorn production server" in result.stdout
    assert called.read_text(encoding="utf-8").startswith("gunicorn ")


def test_entrypoint_uses_flask_dev_server_when_debug_is_on(tmp_path: Path) -> None:
    called = tmp_path / "called.txt"
    _write_fake_executable(
        tmp_path / "gunicorn",
        f"""#!/bin/sh
printf 'gunicorn %s\\n' "$*" > "{called}"
exit 0
""",
    )
    _write_fake_executable(
        tmp_path / "python",
        f"""#!/bin/sh
printf 'python %s\\n' "$*" > "{called}"
exit 0
""",
    )

    env = os.environ.copy()
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    env["FLASK_DEBUG"] = "1"
    env["FLASK_ENV"] = "production"

    result = subprocess.run(
        ["bash", str(ENTRYPOINT)],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0
    assert "Starting Flask development server" in result.stdout
    called_text = called.read_text(encoding="utf-8")
    assert called_text.startswith("python ")
    assert "-m flask run" in called_text
    assert "--reload" in called_text
