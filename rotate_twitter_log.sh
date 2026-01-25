#!/bin/bash
set -e

BASE_DIR="/home/sascha/bots"
LOGFILE="$BASE_DIR/twitter_bot.log"
LOGDIR="$BASE_DIR/logs"

YESTERDAY=$(date -d "yesterday" +"%Y-%m-%d")
ARCHIVED_LOG="$LOGDIR/twitter_bot.log.$YESTERDAY"

cd -- "$BASE_DIR"
source venv/bin/activate

#python store_twitter_logs.py

mkdir -p "$LOGDIR"

if [ -f "$LOGFILE" ]; then
    mv "$LOGFILE" "$ARCHIVED_LOG"
fi

touch "$LOGFILE"
chmod 644 "$LOGFILE"

find "$LOGDIR" -type f -name "twitter_bot.log.*" -mtime +14 -delete

deactivate
