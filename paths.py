from __future__ import annotations

import os
from pathlib import Path


def _resolve_base_dir() -> Path:
    default_dir = Path(__file__).resolve().parent
    requested_raw = os.environ.get("BOTS_BASE_DIR")
    requested = Path(requested_raw).expanduser() if requested_raw else default_dir
    resolved = requested.resolve()
    try:
        resolved.mkdir(parents=True, exist_ok=True)
        return resolved
    except OSError:
        # Fallback keeps startup alive when BOTS_BASE_DIR is invalid/unwritable.
        default_dir.mkdir(parents=True, exist_ok=True)
        return default_dir


BASE_DIR = _resolve_base_dir()
LOG_FILE = str(BASE_DIR / "twitter_bot.log")
LOG_DIR = str(BASE_DIR / "logs")
DATA_FILE = str(BASE_DIR / "data.json")
DEFAULT_DB_PATH = str(BASE_DIR / "nitter_bot.db")
