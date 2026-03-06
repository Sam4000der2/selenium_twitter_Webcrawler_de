## Summary
- switch all bot file loggers from `FileHandler` to `WatchedFileHandler`
- keep log formatting/levels unchanged while ensuring log writers reopen after external rotation
- harden `rotate_twitter_log.sh` with strict shell mode and safer logfile recreation

## Checks
- `python3 -m compileall .` (fails in vendored `venv` site-packages with legacy asyncio syntax)
- `python3 -m compileall -q -x '(^|/)venv($|/)' .`
- `bash -n rotate_twitter_log.sh`

Fixes #4
