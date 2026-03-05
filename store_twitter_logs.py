#!/usr/bin/env python3
import time
from pathlib import Path

from state_store import store_archive_logs
from paths import LOG_FILE

LOGFILE = Path(LOG_FILE)

def main():
    if not LOGFILE.exists():
        return

    entries = []
    now = int(time.time())

    with LOGFILE.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            entries.append({
                "ts": now,
                "line": line
            })

    if entries:
        store_archive_logs(entries)

if __name__ == "__main__":
    main()
