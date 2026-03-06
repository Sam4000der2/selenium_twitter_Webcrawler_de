## Beschreibung
Beim Start von `nitter_bot.py` kann bereits der Modulimport fehlschlagen, wenn `BOTS_BASE_DIR` auf einen nicht schreibbaren/ungeeigneten Pfad zeigt.

## Repro-Schritte
1. `cd /home/sascha/Dokumente/bots`
2. `env BOTS_BASE_DIR=/proc ./venv/bin/python nitter_bot.py --help`

## Logs / Stacktrace
```text
Traceback (most recent call last):
  File "/home/sascha/Dokumente/bots/nitter_bot.py", line 20, in <module>
    import telegram_bot
  File "/home/sascha/Dokumente/bots/telegram_bot.py", line 17, in <module>
    handlers=[WatchedFileHandler(LOG_FILE)],
FileNotFoundError: [Errno 2] No such file or directory: '/proc/twitter_bot.log'
```

## Impact
- Bot startet nicht, selbst `--help` bricht ab.
- Inkonsistentes Fehlerverhalten zwischen Modulen.

## Fix-Idee
- Logging-Setup robust machen: fallback auf beschreibbaren Pfad via `paths.BASE_DIR`/`paths.LOG_FILE`.
- Keine ungeschützten `WatchedFileHandler`-Inits beim Import, wenn Zielpfad ungültig ist.
