#!/bin/bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
BASE_DIR="${BOTS_BASE_DIR:-$REPO_ROOT}"
LOGFILE="${BOT_LOG_FILE:-$BASE_DIR/twitter_bot.log}"
LOGDIR="${BOT_LOG_DIR:-$BASE_DIR/logs}"
PYTHON_BIN="${PYTHON_BIN:-$BASE_DIR/venv/bin/python3}"
STORE_LOGS_MODULE="tools.store_twitter_logs_tool"
LOG_BASENAME="$(basename "$LOGFILE")"

YESTERDAY=$(date -d "yesterday" +"%Y-%m-%d")
ARCHIVED_LOG="$LOGDIR/$LOG_BASENAME.$YESTERDAY"

cd -- "$REPO_ROOT"
if [ -x "$PYTHON_BIN" ]; then
    BOTS_BASE_DIR="$BASE_DIR" "$PYTHON_BIN" -m "$STORE_LOGS_MODULE"
else
    BOTS_BASE_DIR="$BASE_DIR" python3 -m "$STORE_LOGS_MODULE"
fi

mkdir -p "$LOGDIR"
mkdir -p "$(dirname "$LOGFILE")"

if [ -f "$LOGFILE" ]; then
    mv "$LOGFILE" "$ARCHIVED_LOG"
    chmod 600 "$ARCHIVED_LOG"
fi

install -m 600 /dev/null "$LOGFILE"

find "$LOGDIR" -type f -name "$LOG_BASENAME.*" -mtime +14 -delete
