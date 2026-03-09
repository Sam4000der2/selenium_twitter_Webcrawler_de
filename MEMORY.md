# MEMORY

- Issue #56 (2026-03-09): `mastodon_control_bot` reacts only to explicit slash commands outside pending dialogs.
- Pending dialog replies (`ja`/`nein`) continue to work without slash.
- Regression tests for command-trigger behavior are in `tests-unit/test_mastodon_control_bot_commands.py`.
- Issue #57 (2026-03-09): Telegram `data.json` storage migrated into `nitter_bot.db` normalized tables.
- Normalized Telegram schema: `telegram_chats` + `telegram_filter_rules` (no JSON keywords blob).
- `state_store.migrate_telegram_json_to_db(...)` imports legacy `data.json` into DB when needed.
- `modules/telegram_bot_module.py` and `bots/telegram_control_bot.py` read/write Telegram state via `state_store`.
- Telegram migration tests: `tests-unit/test_storage_telegram_migration.py`.
- Dedicated CLI migration script: `tools/migrate_telegram_data_json_tool.py` (`--dry-run`, `--force`, optional `--db-path`).
- Utility layout: Python tools in `tools/` (`*_tool.py`), shell helpers in `scripts/`, static templates in `config/`.
- Central log level config: `BOTS_LOG_LEVEL` (fallback `LOG_LEVEL`) via `modules.paths_module.LOG_LEVEL`.
- New default settings file: `config/default_settings.json` (central defaults for `log_level`, DB/log/data paths, poll and retention values).
- Default SQLite location moved to `config/nitter_bot.db`; legacy root DB path is auto-migrated/fallback-safe in `modules/storage_module.py`.
- `config/data.json.example` removed; legacy `data.json` is now documented as optional local runtime file without committed template.
- `bots/nitter_bot.py` and `bots/bsky_bot.py` now read core defaults from `modules.paths_module` constants.
- Canonical runtime layout: `bots/` for executable bots, `modules/` for reusable modules.
- Naming convention: bots use `*_bot.py` / `*_control_bot.py`; modules use `_module.py`.
- `modules/mastodon_bot_module.py` and `modules/telegram_bot_module.py` are modules (not bot entry files).
