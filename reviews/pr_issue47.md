## Summary
Add uppercase-first Telegram ENV handling (`TELEGRAM_TOKEN`, `TELEGRAM_ADMIN`) with backward compatibility for legacy lower-case names, and align README examples.

## Checks
- `env -u telegram_token -u telegram_admin TELEGRAM_TOKEN=bar TELEGRAM_ADMIN=2 ./venv/bin/python -c 'import telegram_bot; print(telegram_bot.BOT_TOKEN, telegram_bot.admin)'`
- `env TELEGRAM_TOKEN=bar TELEGRAM_ADMIN=2 ./venv/bin/python -c 'import telegram_control_bot; print(telegram_control_bot.BOT_TOKEN, telegram_control_bot.admin)'`
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
- `./venv/bin/pytest -q tests tests-unit`
- `./venv/bin/ruff check .`

## Modules touched
- `telegram_bot.py`
- `telegram_control_bot.py`
- `README.md`

Fixes #47
