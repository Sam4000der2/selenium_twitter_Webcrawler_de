Issue #57 Verifikation (unabhaengig)

Scope:
- `data.json`-Daten in `nitter_bot.db` als normalisierte Tabellen/Spalten.
- Kein Telegram-Keywords-JSON-Blob mehr als aktive Persistenz.
- Telegram Bot + Telegram Control Bot greifen weiterhin konsistent auf denselben Datenbestand zu.

Akzeptanzkriterien:
1. Telegram-Daten liegen in separaten Tabellen/Spalten in SQLite.
2. Telegram Bot liest dieselben Regeln/aktiven Nutzer wie zuvor, jetzt aus DB.
3. Telegram Control Bot schreibt/liest Regeln und aktive Nutzer aus DB (statt `data.json`).
4. Migration von Legacy-`data.json`/Legacy-Tabelle ist vorhanden.

Durchgefuehrte Verifikation:
- Codepruefung:
  - `storage.py`: neue normalisierte Tabellen `telegram_chats` und `telegram_filter_rules`; Legacy-Tabelle `telegram_filters` wird migriert und entfernt.
  - `state_store.py`: `migrate_telegram_json_to_db(...)` importiert Legacy-`data.json`; `load_telegram_data`/`save_telegram_data` nutzen DB.
  - `telegram_bot.py`: `load_data()` liest via `state_store.load_telegram_data()`.
  - `telegram_control_bot.py`: `load_data()`/`save_data()` nutzen `state_store` statt Dateizugriff.
- Schema-Check:
  - `venv/bin/python - <<'PY' ...` bestaetigt Telegram-Tabellen: `telegram_chats`, `telegram_filter_rules`.
- Tests:
  - `venv/bin/python -m pytest -q -p no:cacheprovider tests tests-unit` -> `13 passed`
  - `venv/bin/ruff check storage.py state_store.py telegram_bot.py telegram_control_bot.py tests-unit/test_storage_telegram_migration.py` -> `All checks passed`
  - Neue Migrationstests in `tests-unit/test_storage_telegram_migration.py` decken Legacy-Tabellenmigration, normalisierte Speicherung und `data.json`-Migration ab.

Ergebnis:
0 Findings
