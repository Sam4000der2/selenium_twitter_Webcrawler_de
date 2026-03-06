## Beschreibung
`nitter_bot.py` (und indirekt `telegram_control_bot.py`) importieren Module mit starken Side-Effects (Gemini/DB-Init) bereits beim Start, wodurch ein ungültiger DB-Pfad den Start selbst für `--help` killt.

## Repro-Schritte
1. `cd /home/sascha/Dokumente/bots`
2. `env NITTER_DB_PATH=/proc/nitter.db ./venv/bin/python nitter_bot.py --help`

## Logs / Stacktrace
```text
Traceback (most recent call last):
  File "/home/sascha/Dokumente/bots/nitter_bot.py", line 21, in <module>
    import mastodon_bot
  File "/home/sascha/Dokumente/bots/mastodon_bot.py", line 87, in <module>
    gemini_manager = GeminiModelManager(client)
...
sqlite3.OperationalError: unable to open database file
```

## Impact
- CLI/Smoke-Checks und einfache Diagnosekommandos brechen vorzeitig.
- Koppelt unabhängig erscheinende Bots unnötig stark über Importzeit.

## Fix-Idee
- Schwere Modulimporte (`telegram_bot`, `mastodon_bot`) in `nitter_bot.py` lazy in Laufzeitpfade verschieben.
- Optionales Entkoppeln ähnlicher Side-Effects in Control-Bots.
