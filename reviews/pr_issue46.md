## Summary
Harden log handling by tightening rotation file permissions and preventing archived log files from being accidentally committed.

## Checks
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
- `./venv/bin/pytest -q tests tests-unit`
- `./venv/bin/ruff check .`
- `BOTS_BASE_DIR=$(mktemp -d) PYTHON_BIN=/bin/true bash rotate_twitter_log.sh` + `stat`

## Modules touched
- `.gitignore`
- `rotate_twitter_log.sh`

Fixes #46
