from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any


SETTINGS_FILE_ENV = "BOTS_DEFAULT_SETTINGS_FILE"
SETTINGS_FILE_RELATIVE = Path("config/default_settings.json")


def _ensure_writable_dir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    probe_file = path / ".bots_write_probe"
    try:
        probe_file.touch(exist_ok=True)
    except OSError:
        return False
    finally:
        try:
            probe_file.unlink()
        except OSError:
            pass
    return True


def _resolve_base_dir() -> Path:
    default_dir = Path(__file__).resolve().parents[1]
    requested_raw = os.environ.get("BOTS_BASE_DIR")
    requested = Path(requested_raw).expanduser() if requested_raw else default_dir
    try:
        resolved = requested.resolve()
    except OSError:
        resolved = default_dir

    if _ensure_writable_dir(resolved):
        return resolved

    if _ensure_writable_dir(default_dir):
        return default_dir

    return Path.cwd()


def _resolve_settings_path(base_dir: Path) -> Path:
    requested_raw = os.environ.get(SETTINGS_FILE_ENV)
    if requested_raw and requested_raw.strip():
        requested = Path(requested_raw.strip()).expanduser()
        return requested if requested.is_absolute() else base_dir / requested
    return base_dir / SETTINGS_FILE_RELATIVE


def _load_default_settings(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _resolve_path_setting(base_dir: Path, raw_value: Any, fallback_relative: Path) -> Path:
    fallback = base_dir / fallback_relative
    if not isinstance(raw_value, str) or not raw_value.strip():
        return fallback
    requested = Path(raw_value.strip()).expanduser()
    return requested if requested.is_absolute() else base_dir / requested


def _parse_int_setting(raw_value: Any, default: int, *, min_value: int | None = None) -> int:
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = default
    if min_value is not None and value < min_value:
        return min_value
    return value


BASE_DIR = _resolve_base_dir()
SETTINGS_FILE = _resolve_settings_path(BASE_DIR)
DEFAULT_SETTINGS = _load_default_settings(SETTINGS_FILE)

LOG_FILE = str(
    _resolve_path_setting(
        BASE_DIR,
        DEFAULT_SETTINGS.get("log_file"),
        Path("twitter_bot.log"),
    )
)
LOG_DIR = str(
    _resolve_path_setting(
        BASE_DIR,
        DEFAULT_SETTINGS.get("log_dir"),
        Path("logs"),
    )
)
DATA_FILE = str(
    _resolve_path_setting(
        BASE_DIR,
        DEFAULT_SETTINGS.get("data_file"),
        Path("data.json"),
    )
)
DEFAULT_DB_PATH = str(
    _resolve_path_setting(
        BASE_DIR,
        DEFAULT_SETTINGS.get("db_path"),
        Path("config/nitter_bot.db"),
    )
)
LEGACY_DB_PATH = str(BASE_DIR / "nitter_bot.db")

DEFAULT_LIVE_LOG_RETENTION_DAYS = _parse_int_setting(
    DEFAULT_SETTINGS.get("live_log_retention_days"),
    7,
    min_value=0,
)
DEFAULT_ARCHIVE_LOG_RETENTION_DAYS = _parse_int_setting(
    DEFAULT_SETTINGS.get("archive_log_retention_days"),
    90,
    min_value=0,
)
DEFAULT_NITTER_POLL_INTERVAL = _parse_int_setting(
    DEFAULT_SETTINGS.get("nitter_poll_interval_seconds"),
    60,
    min_value=1,
)
DEFAULT_NITTER_HISTORY_LIMIT = _parse_int_setting(
    DEFAULT_SETTINGS.get("nitter_history_limit"),
    200,
    min_value=1,
)
DEFAULT_NITTER_MAX_ITEM_AGE_SECONDS = _parse_int_setting(
    DEFAULT_SETTINGS.get("nitter_max_item_age_seconds"),
    2 * 60 * 60,
    min_value=0,
)
DEFAULT_BSKY_MIN_KEEP = _parse_int_setting(
    DEFAULT_SETTINGS.get("bsky_min_keep"),
    20,
    min_value=1,
)
DEFAULT_BSKY_MAX_KEEP_CAP = _parse_int_setting(
    DEFAULT_SETTINGS.get("bsky_max_keep_cap"),
    1000,
    min_value=1,
)
DEFAULT_BSKY_MAX_ENTRY_AGE_SECONDS = _parse_int_setting(
    DEFAULT_SETTINGS.get("bsky_max_entry_age_seconds"),
    3 * 60 * 60,
    min_value=0,
)


def parse_log_level(value: str | None, default: int = logging.INFO) -> int:
    if value is None:
        return default
    raw = str(value).strip()
    if not raw:
        return default
    if raw.isdigit():
        return int(raw)
    parsed = logging.getLevelName(raw.upper())
    return parsed if isinstance(parsed, int) else default


def get_configured_log_level(default: int = logging.INFO) -> int:
    configured = os.environ.get("BOTS_LOG_LEVEL") or os.environ.get("LOG_LEVEL")
    if configured is None or not str(configured).strip():
        configured = DEFAULT_SETTINGS.get("log_level")
    return parse_log_level(
        configured,
        default=default,
    )


LOG_LEVEL = get_configured_log_level()
