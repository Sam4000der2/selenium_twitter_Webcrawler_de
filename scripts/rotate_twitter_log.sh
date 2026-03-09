#!/bin/bash
set -euo pipefail
umask 077

BASE_DIR="${BOTS_BASE_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOGFILE="${BOT_LOG_FILE:-$BASE_DIR/twitter_bot.log}"
LOGDIR="$BASE_DIR/logs"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/venv/bin/python3}"

YESTERDAY=$(date -d "yesterday" +"%Y-%m-%d")
ARCHIVED_LOG="$LOGDIR/twitter_bot.log.$YESTERDAY"

cd -- "$BASE_DIR"
if [ -x "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" -m tools.store_twitter_logs_tool
else
    python3 -m tools.store_twitter_logs_tool
fi

mkdir -p "$LOGDIR"
mkdir -p "$(dirname "$LOGFILE")"

if [ -f "$LOGFILE" ]; then
    mv "$LOGFILE" "$ARCHIVED_LOG"
    chmod 600 "$ARCHIVED_LOG"
fi

install -m 600 /dev/null "$LOGFILE"

find "$LOGDIR" -type f -name "twitter_bot.log.*" -mtime +14 -delete
