## Summary
Avoid import-time DB/Gemini side-effects on CLI/help code paths by lazily loading delivery modules in `nitter_bot` only when sending/retry processing is needed.

## Checks
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
- `./venv/bin/pytest -q tests tests-unit`
- `./venv/bin/ruff check .`
- `env NITTER_DB_PATH=/proc/nitter.db ./venv/bin/python nitter_bot.py --help`

## Modules touched
- `nitter_bot.py`

Fixes #45
