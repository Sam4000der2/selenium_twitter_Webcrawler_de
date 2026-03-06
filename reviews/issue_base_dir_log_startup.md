## Problem
Beim Start mit gesetztem, aber nicht existierendem `BOTS_BASE_DIR` bricht der Bot früh mit `FileNotFoundError` ab, weil der Logpfad vor dem Logging-Handler nicht sichergestellt wird.

## Repro Steps
1. `cd /home/sascha/Dokumente/bots`
2. `env BOTS_BASE_DIR=/tmp/missing-nonexistent-bots ./venv/bin/python nitter_bot.py --help`

## Logs / Stacktrace
```text
Traceback (most recent call last):
  File "/home/sascha/Dokumente/bots/nitter_bot.py", line 20, in <module>
    import telegram_bot
  File "/home/sascha/Dokumente/bots/telegram_bot.py", line 17, in <module>
    handlers=[WatchedFileHandler(LOG_FILE)],
...
FileNotFoundError: [Errno 2] No such file or directory: '/tmp/missing-nonexistent-bots/twitter_bot.log'
```

## Impact
- Ein einziger fehlerhafter oder leerer Zielpfad verhindert den kompletten Start.
- Erhöhte Betriebsstörungen bei Deployments und Service-Restarts.

## Fix Idea
- Zentrale Pfadauflösung in `paths.py` robust machen:
  - `BOTS_BASE_DIR` auflösen,
  - Verzeichnis bei Bedarf anlegen,
  - bei Fehlern auf sicheren Fallback (Repo-Verzeichnis) zurückfallen.
- Bestehende Aufrufer behalten unveränderte API (`LOG_FILE`, `DEFAULT_DB_PATH`).

## Acceptance Criteria
- Startup mit ungültigem/nicht vorhandenem `BOTS_BASE_DIR` führt nicht zu `FileNotFoundError` beim Logger.
- Fallback/Auto-create ist nachvollziehbar (Warning-Log oder deterministisches Verhalten).
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .` ist erfolgreich.
- `./venv/bin/pytest -q` ist erfolgreich.
