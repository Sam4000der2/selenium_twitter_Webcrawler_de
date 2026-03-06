## Problem
`mastodon_bot.py` parst mehrere numerische ENV-Werte mit direktem `int(...)`. Ungültige Werte führen zu `ValueError` bereits beim Import und verhindern den Start des Bots.

## Repro Steps
1. `cd /home/sascha/Dokumente/bots`
2. `env MASTODON_CONTROL_EVENT_PORT=abc ./venv/bin/python -c "import mastodon_bot"`
3. Optional zusätzlich: `env MASTODON_VERSION_CACHE_MAX_AGE_SECONDS=abc ./venv/bin/python -c "import mastodon_bot"`

## Logs / Stacktrace
```text
Traceback (most recent call last):
  File "<string>", line 1, in <module>
  File "/home/sascha/Dokumente/bots/mastodon_bot.py", line 154, in <module>
    EVENT_PORT = int(os.environ.get("MASTODON_CONTROL_EVENT_PORT", "8123"))
ValueError: invalid literal for int() with base 10: 'abc'
```

## Impact
- Service startet nicht bei fehlerhaftem ENV-Input.
- Operatives Risiko bei Deployments/Config-Änderungen.

## Fix Idea
- Robuste zentrale Parserfunktion für Integer-ENV-Werte einführen (Fallback-Default, optional min/max Clamp, Warn-Logging).
- Auf alle numerischen ENV-Einstiege in `mastodon_bot.py` anwenden.

## Acceptance Criteria
- Ungültige numerische ENV-Werte crashen den Prozess nicht mehr.
- Es wird ein nachvollziehbares Warning geloggt.
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .` ist erfolgreich.
- `./venv/bin/pytest -q` ist erfolgreich.
