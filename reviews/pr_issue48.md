## Summary
Refactor duplicated control-bot helper logic into a shared `control_bot_utils.py` module and route both control bots through it.

## Checks
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
- `./venv/bin/pytest -q tests tests-unit`
- `./venv/bin/ruff check .`
- `./venv/bin/python - <<'PY'\nimport telegram_control_bot\nimport mastodon_control_bot\nprint("ok")\nPY`

## Modules touched
- `control_bot_utils.py`
- `telegram_control_bot.py`
- `mastodon_control_bot.py`

Fixes #48
