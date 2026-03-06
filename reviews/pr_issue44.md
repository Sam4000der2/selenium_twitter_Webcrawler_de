## Summary
Fixes startup crash when `BOTS_BASE_DIR` points to a non-writable directory by hardening base-dir resolution and making `nitter_bot` consume the shared resilient log path.

## Checks
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
- `./venv/bin/pytest -q tests tests-unit`
- `./venv/bin/ruff check .`

## Modules touched
- `paths.py`
- `nitter_bot.py`

Fixes #44
