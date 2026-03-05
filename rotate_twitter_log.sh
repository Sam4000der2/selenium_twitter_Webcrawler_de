#!/bin/bash
set -e

BASE_DIR="${BOTS_BASE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
LOGFILE="$BASE_DIR/twitter_bot.log"
LOGDIR="$BASE_DIR/logs"
PYTHON_BIN="$BASE_DIR/venv/bin/python3"

YESTERDAY=$(date -d "yesterday" +"%Y-%m-%d")
ARCHIVED_LOG="$LOGDIR/twitter_bot.log.$YESTERDAY"

cd -- "$BASE_DIR"
if [ -x "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" store_twitter_logs.py
else
    python3 store_twitter_logs.py
fi

mkdir -p "$LOGDIR"

if [ -f "$LOGFILE" ]; then
    mv "$LOGFILE" "$ARCHIVED_LOG"
fi

touch "$LOGFILE"
chmod 644 "$LOGFILE"

find "$LOGDIR" -type f -name "twitter_bot.log.*" -mtime +14 -delete
