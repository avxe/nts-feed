from __future__ import annotations

from pathlib import Path


MAKEFILE = (Path(__file__).resolve().parents[1] / "Makefile").read_text(encoding="utf-8")


def test_quickstart_uses_production_style_stack() -> None:
    assert "quickstart: ensure-env env-check runtime-bootstrap docker-build docker-up" in MAKEFILE
    assert "docker-up: env-check runtime-bootstrap docker-check ## Start the production-style local stack" in MAKEFILE
    assert "docker compose -f docker-compose.yml up -d" in MAKEFILE


def test_dev_stack_stays_explicit() -> None:
    assert "quickstart-dev: ensure-env env-check runtime-bootstrap docker-build docker-dev" in MAKEFILE
    assert "docker-dev: env-check runtime-bootstrap docker-check-dev ## Foreground dev stack with hot reload" in MAKEFILE
    assert "docker compose -f docker-compose.yml -f docker-compose.dev.yml up" in MAKEFILE
