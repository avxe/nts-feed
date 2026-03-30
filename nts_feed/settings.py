"""Helpers for reading and writing lightweight app settings."""

from __future__ import annotations

import json
import os
from pathlib import Path


DEFAULT_SETTINGS = {
    "TRIM_ENABLED": True,
    "TRIM_START_SECONDS": 12,
    "TRIM_END_SECONDS": 12,
    "YOUTUBE_API_KEY": "",
    "DISCOGS_TOKEN": "",
    "LASTFM_API_KEY": "",
    "LASTFM_API_SECRET": "",
}

BOOLEAN_KEYS = {"TRIM_ENABLED"}
INTEGER_KEYS = {"TRIM_START_SECONDS", "TRIM_END_SECONDS"}


def get_settings_path() -> Path:
    """Return the JSON settings file path."""
    custom_path = os.getenv("NTS_SETTINGS_PATH")
    if custom_path:
        return Path(custom_path)
    return Path(__file__).resolve().parent.parent / "data" / "settings.json"


def load_raw_settings() -> dict:
    """Return settings saved on disk without defaults."""
    settings_path = get_settings_path()
    if not settings_path.exists():
        return {}

    try:
        with settings_path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}

    if not isinstance(data, dict):
        return {}

    legacy_trim = data.get("TRIM_DURATION")
    cleaned = {}
    for key, value in data.items():
        if key not in DEFAULT_SETTINGS:
            continue
        cleaned[key] = _coerce_setting_value(key, value)

    if (
        "TRIM_START_SECONDS" not in cleaned
        and "TRIM_END_SECONDS" not in cleaned
        and legacy_trim is not None
    ):
        try:
            legacy_value = _coerce_integer(legacy_trim, "TRIM_START_SECONDS")
        except ValueError:
            legacy_value = DEFAULT_SETTINGS["TRIM_START_SECONDS"]
        cleaned["TRIM_START_SECONDS"] = legacy_value
        cleaned["TRIM_END_SECONDS"] = legacy_value

    return cleaned


def load_settings() -> dict:
    """Return the effective settings with defaults and env overrides."""
    settings = DEFAULT_SETTINGS.copy()
    settings.update(load_raw_settings())

    for key in DEFAULT_SETTINGS:
        env_value = os.getenv(key)
        if env_value in (None, ""):
            continue
        settings[key] = _coerce_setting_value(key, env_value)

    if (
        os.getenv("TRIM_START_SECONDS") in (None, "")
        and os.getenv("TRIM_END_SECONDS") in (None, "")
    ):
        legacy_env = os.getenv("TRIM_DURATION")
        if legacy_env not in (None, ""):
            try:
                legacy_value = _coerce_integer(legacy_env, "TRIM_START_SECONDS")
                settings["TRIM_START_SECONDS"] = legacy_value
                settings["TRIM_END_SECONDS"] = legacy_value
            except ValueError:
                pass

    return settings


def save_settings(updates: dict) -> dict:
    """Persist supported setting updates and return the effective values."""
    if not isinstance(updates, dict):
        raise ValueError("Settings payload must be an object.")

    saved_settings = load_raw_settings()
    for key, value in updates.items():
        if key not in DEFAULT_SETTINGS:
            continue
        saved_settings[key] = _coerce_setting_value(key, value)

    settings_path = get_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    with settings_path.open("w", encoding="utf-8") as fh:
        json.dump(saved_settings, fh, indent=2, sort_keys=True)

    return load_settings()


def apply_saved_settings_to_env() -> None:
    """Copy saved settings into the environment when no runtime value exists."""
    for key, value in load_raw_settings().items():
        if os.getenv(key) not in (None, ""):
            continue
        os.environ[key] = _stringify_setting_value(value)


def _coerce_setting_value(key: str, value):
    if key in BOOLEAN_KEYS:
        return _coerce_boolean(value, key)
    if key in INTEGER_KEYS:
        return _coerce_integer(value, key)
    if value is None:
        return ""
    return str(value).strip()


def _coerce_boolean(value, key: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{key} must be true or false.")


def _coerce_integer(value, key: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a whole number.") from exc
    if parsed < 0:
        raise ValueError(f"{key} must be 0 or greater.")
    return parsed


def _stringify_setting_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
