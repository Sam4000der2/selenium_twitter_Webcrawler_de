# MEMORY

- Repo path: `/home/sascha/Dokumente/bots` may be restored via fresh clone after accidental deletions.
- Reliable tests command in this workspace: `venv/bin/python -m pytest tests tests-unit`.
- Issue #56 (2026-03-09): `mastodon_control_bot` should ignore normal mentions and only react to explicit slash commands outside pending dialogs.
- Pending dialog replies (`ja`/`nein`) must continue to work without slash.
- Regression tests for command-trigger behavior are in `tests-unit/test_mastodon_control_bot_commands.py`.
