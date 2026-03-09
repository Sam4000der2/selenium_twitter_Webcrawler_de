# MEMORY

- Issue #56 (2026-03-09): `mastodon_control_bot` reacts only to explicit slash commands outside pending dialogs.
- Pending dialog replies (`ja`/`nein`) continue to work without slash.
- Regression tests for command-trigger behavior are in `tests-unit/test_mastodon_control_bot_commands.py`.
