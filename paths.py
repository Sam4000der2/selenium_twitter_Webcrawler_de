from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(os.environ.get("BOTS_BASE_DIR") or Path(__file__).resolve().parent).resolve()
LOG_FILE = str(BASE_DIR / "twitter_bot.log")
LOG_DIR = str(BASE_DIR / "logs")
DATA_FILE = str(BASE_DIR / "data.json")
DEFAULT_DB_PATH = str(BASE_DIR / "nitter_bot.db")
