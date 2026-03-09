import json

import migrate_telegram_data_json as migrator
from modules import state_store_module as state_store
from modules import storage_module as storage


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "nitter_bot.db"
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    monkeypatch.setattr(storage, "_initialized", False)
    monkeypatch.setattr(state_store, "_TELEGRAM_FILE_MIGRATION_CHECKED", True)
    return db_path


def test_run_migration_moves_data_into_db(monkeypatch, tmp_path) -> None:
    db_path = _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {"123": True}, "filter_rules": {"123": ["S42", "U1"]}}),
        encoding="utf-8",
    )

    result = migrator.run_migration(data_file=str(data_file), db_path=str(db_path))
    assert result.status == "migrated"
    assert result.source_chats == 1
    assert result.source_rules == 2
    assert result.db_chats_after == 1
    assert result.db_rules_after == 2

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {"123": True}
    assert loaded["filter_rules"] == {"123": ["S42", "U1"]}


def test_run_migration_dry_run_does_not_write(monkeypatch, tmp_path) -> None:
    db_path = _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {"123": True}, "filter_rules": {"123": ["ring"]}}),
        encoding="utf-8",
    )

    result = migrator.run_migration(data_file=str(data_file), db_path=str(db_path), dry_run=True)
    assert result.status == "dry_run"

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {}
    assert loaded["filter_rules"] == {}


def test_run_migration_skips_when_db_has_data_without_force(monkeypatch, tmp_path) -> None:
    db_path = _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()
    state_store.save_telegram_data({"chat_ids": {"999": True}, "filter_rules": {"999": ["old"]}})

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {"123": True}, "filter_rules": {"123": ["new"]}}),
        encoding="utf-8",
    )

    result = migrator.run_migration(data_file=str(data_file), db_path=str(db_path))
    assert result.status == "skipped"

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {"999": True}
    assert loaded["filter_rules"] == {"999": ["old"]}


def test_run_migration_force_overwrites_existing_db_data(monkeypatch, tmp_path) -> None:
    db_path = _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()
    state_store.save_telegram_data({"chat_ids": {"999": True}, "filter_rules": {"999": ["old"]}})

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {"123": True}, "filter_rules": {"123": ["new"]}}),
        encoding="utf-8",
    )

    result = migrator.run_migration(data_file=str(data_file), db_path=str(db_path), force=True)
    assert result.status == "migrated"
    assert result.db_chats_before == 1
    assert result.db_rules_before == 1
    assert result.db_chats_after == 1
    assert result.db_rules_after == 1

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {"123": True}
    assert loaded["filter_rules"] == {"123": ["new"]}


def test_run_migration_does_not_activate_chat_when_only_rules_exist(monkeypatch, tmp_path) -> None:
    db_path = _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {}, "filter_rules": {"123": ["new"]}}),
        encoding="utf-8",
    )

    result = migrator.run_migration(data_file=str(data_file), db_path=str(db_path))
    assert result.status == "migrated"

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {}
    assert loaded["filter_rules"] == {"123": ["new"]}


def test_run_migration_uses_explicit_source_without_default_auto_migration(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    default_data_file = tmp_path / "default_data.json"
    default_data_file.write_text(
        json.dumps({"chat_ids": {"999": True}, "filter_rules": {"999": ["default"]}}),
        encoding="utf-8",
    )
    explicit_data_file = tmp_path / "explicit_data.json"
    explicit_data_file.write_text(
        json.dumps({"chat_ids": {"123": True}, "filter_rules": {"123": ["explicit"]}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(state_store, "DEFAULT_DATA_FILE", str(default_data_file))
    monkeypatch.setattr(state_store, "_TELEGRAM_FILE_MIGRATION_CHECKED", False)

    result = migrator.run_migration(data_file=str(explicit_data_file))
    assert result.status == "migrated"

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {"123": True}
    assert loaded["filter_rules"] == {"123": ["explicit"]}
