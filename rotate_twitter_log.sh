#!/bin/bash
set -euo pipefail

BASE_DIR="${BOTS_BASE_DIR:-/home/sascha/bots}"
LOGFILE="${BOT_LOG_FILE:-$BASE_DIR/twitter_bot.log}"
LOGDIR="${TWITTER_LOG_ARCHIVE_DIR:-$BASE_DIR/logs}"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/venv/bin/python3}"

YESTERDAY=$(date -d "yesterday" +"%Y-%m-%d")
ARCHIVED_LOG="$LOGDIR/twitter_bot.log.$YESTERDAY"

cd -- "$BASE_DIR"
if [ -x "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" store_twitter_logs.py
else
    python3 store_twitter_logs.py
fi

mkdir -p "$LOGDIR"
mkdir -p "$(dirname "$LOGFILE")"

if [ -f "$LOGFILE" ]; then
    mv "$LOGFILE" "$ARCHIVED_LOG"
fi

install -m 644 /dev/null "$LOGFILE"

find "$LOGDIR" -type f -name "twitter_bot.log.*" -mtime +14 -delete
