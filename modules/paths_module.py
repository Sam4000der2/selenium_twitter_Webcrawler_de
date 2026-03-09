from __future__ import annotations

import logging
import os
from pathlib import Path


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
    # Default bleibt der Projekt-Root (ein Level über modules/),
    # damit bestehende DB-/Log-/Data-Pfade kompatibel bleiben.
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


BASE_DIR = _resolve_base_dir()
LOG_FILE = str(BASE_DIR / "twitter_bot.log")
LOG_DIR = str(BASE_DIR / "logs")
DATA_FILE = str(BASE_DIR / "data.json")
DEFAULT_DB_PATH = str(BASE_DIR / "nitter_bot.db")


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
    return parse_log_level(
        os.environ.get("BOTS_LOG_LEVEL") or os.environ.get("LOG_LEVEL"),
        default=default,
    )


LOG_LEVEL = get_configured_log_level()
