from __future__ import annotations

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
    default_dir = Path(__file__).resolve().parent
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
