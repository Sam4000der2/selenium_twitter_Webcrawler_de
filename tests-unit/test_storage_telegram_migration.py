import json
import sqlite3

import state_store
import storage


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "nitter_bot.db"
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    monkeypatch.setattr(storage, "_initialized", False)
    monkeypatch.setattr(state_store, "_TELEGRAM_FILE_MIGRATION_CHECKED", False)
    return db_path


def test_storage_migrates_legacy_telegram_filters_to_normalized_tables(monkeypatch, tmp_path) -> None:
    db_path = _configure_temp_db(monkeypatch, tmp_path)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE telegram_filters (
                chat_id INTEGER PRIMARY KEY,
                keywords TEXT NOT NULL DEFAULT '[]',
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO telegram_filters (chat_id, keywords, updated_at) VALUES (?, ?, ?)",
            (111, json.dumps(["S42", "U1"]), 1),
        )
        conn.commit()

    storage.init_db()

    data = storage.read_value("telegram_config", "chat_config", {})
    assert data["chat_ids"] == {"111": True}
    assert data["filter_rules"] == {"111": ["S42", "U1"]}

    with storage.get_connection() as conn:
        legacy_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='telegram_filters'"
        ).fetchone()
        assert legacy_table is None
        rows = conn.execute(
            "SELECT chat_id, keyword FROM telegram_filter_rules ORDER BY rowid"
        ).fetchall()
    assert rows == [(111, "S42"), (111, "U1")]


def test_storage_write_telegram_uses_normalized_rows(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    storage.write_value(
        "telegram_config",
        "chat_config",
        {
            "chat_ids": {"123": True},
            "filter_rules": {"123": ["foo", "bar", "foo"]},
        },
    )

    data = storage.read_value("telegram_config", "chat_config", {})
    assert data["chat_ids"] == {"123": True}
    assert data["filter_rules"] == {"123": ["foo", "bar"]}

    with storage.get_connection() as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(telegram_filter_rules)").fetchall()]
        rows = conn.execute(
            "SELECT chat_id, keyword FROM telegram_filter_rules ORDER BY rowid"
        ).fetchall()

    assert cols == ["chat_id", "keyword", "updated_at"]
    assert rows == [(123, "foo"), (123, "bar")]


def test_state_store_migrates_data_json_into_db(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {"777": True}, "filter_rules": {"777": ["alex", "ring"]}}),
        encoding="utf-8",
    )

    migrated = state_store.migrate_telegram_json_to_db(str(data_file))
    assert migrated is True

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {"777": True}
    assert loaded["filter_rules"] == {"777": ["alex", "ring"]}

    migrated_again = state_store.migrate_telegram_json_to_db(str(data_file))
    assert migrated_again is False


def test_state_store_migration_keeps_stopped_chats_inactive(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    data_file = tmp_path / "data.json"
    data_file.write_text(
        json.dumps({"chat_ids": {}, "filter_rules": {"777": ["alex"]}}),
        encoding="utf-8",
    )

    migrated = state_store.migrate_telegram_json_to_db(str(data_file))
    assert migrated is True

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {}
    assert loaded["filter_rules"] == {"777": ["alex"]}


def test_state_store_keeps_rules_without_reactivating_chat(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    state_store.save_telegram_data(
        {
            "chat_ids": {"123": True},
            "filter_rules": {"123": ["tram"]},
        }
    )

    state_store.save_telegram_data(
        {
            "chat_ids": {},
            "filter_rules": {"123": ["tram"]},
        }
    )

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {}
    assert loaded["filter_rules"] == {"123": ["tram"]}

    with storage.get_connection() as conn:
        chats = conn.execute("SELECT chat_id FROM telegram_chats").fetchall()
        rules = conn.execute("SELECT chat_id, keyword FROM telegram_filter_rules").fetchall()

    assert chats == []
    assert rules == [(123, "tram")]
