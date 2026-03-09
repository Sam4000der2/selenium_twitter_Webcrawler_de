# MEMORY

- Issue #56 (2026-03-09): `mastodon_control_bot` reacts only to explicit slash commands outside pending dialogs.
- Pending dialog replies (`ja`/`nein`) continue to work without slash.
- Regression tests for command-trigger behavior are in `tests-unit/test_mastodon_control_bot_commands.py`.
- Issue #57 (2026-03-09): Telegram `data.json` storage migrated into `nitter_bot.db` normalized tables.
- Normalized Telegram schema: `telegram_chats` + `telegram_filter_rules` (no JSON keywords blob).
- `state_store.migrate_telegram_json_to_db(...)` imports legacy `data.json` into DB when needed.
- `telegram_bot.py` and `telegram_control_bot.py` now read/write Telegram state via `state_store`.
- Telegram migration tests: `tests-unit/test_storage_telegram_migration.py`.
