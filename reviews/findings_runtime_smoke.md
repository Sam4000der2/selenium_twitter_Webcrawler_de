# Runtime / Smoke Findings

## Summary
Es wurden zwei reproduzierbare Runtime-Fehler gefunden, die den Bot-Start direkt abbrechen.

## Executed commands + key output
- `env MASTODON_CONTROL_EVENT_PORT=abc ./venv/bin/python -c "import mastodon_bot"`
  - `ValueError: invalid literal for int() with base 10: 'abc'`
- `env MASTODON_VERSION_CACHE_MAX_AGE_SECONDS=abc ./venv/bin/python -c "import mastodon_bot"`
  - `ValueError: invalid literal for int() with base 10: 'abc'`
- `env BOTS_BASE_DIR=/tmp/missing-nonexistent-bots ./venv/bin/python nitter_bot.py --help`
  - `FileNotFoundError: ... '/tmp/missing-nonexistent-bots/twitter_bot.log'`

## Findings
1. **HIGH** - `mastodon_bot.py:94`, `mastodon_bot.py:154`, `mastodon_bot.py:173`, `mastodon_bot.py:174`, `mastodon_bot.py:183`
- Beschreibung: Numerische ENV-Werte werden mit direktem `int(...)` geparst. Ungültige Werte crashen den Prozess bereits beim Import.
- Repro:
  1. `env MASTODON_CONTROL_EVENT_PORT=abc ./venv/bin/python -c "import mastodon_bot"`
  2. Ergebnis: sofortiger `ValueError` + Exit-Code != 0.
- Impact: Service startet nicht bei fehlerhaft gesetzten ENV-Werten.

2. **HIGH** - `paths.py:7`, `telegram_bot.py:17`, `nitter_bot.py:103`
- Beschreibung: Bei gesetztem, nicht existierendem `BOTS_BASE_DIR` wird kein Verzeichnis erstellt. `WatchedFileHandler` wirft dadurch `FileNotFoundError` beim Startup.
- Repro:
  1. `env BOTS_BASE_DIR=/tmp/missing-nonexistent-bots ./venv/bin/python nitter_bot.py --help`
  2. Ergebnis: `FileNotFoundError` für `twitter_bot.log`.
- Impact: Startup bricht frühzeitig ab; erhöhte Betriebsstörung bei Fehldeployments.

## Suggested fix ideas
- Zentrale robuste Integer-ENV-Parser in `mastodon_bot.py` einführen (Fallback + optional Clamp + Warning).
- In `paths.py` Basis-/Log-Verzeichnis robust auflösen und bei fehlendem Pfad anlegen oder auf sicheren Fallback zurückfallen.
