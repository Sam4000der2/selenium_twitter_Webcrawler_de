from modules import state_store_module as state_store
from modules import storage_module as storage


def _configure_temp_db(monkeypatch, tmp_path):
    db_path = tmp_path / "nitter_bot.db"
    monkeypatch.setattr(storage, "DB_PATH", str(db_path))
    monkeypatch.setattr(storage, "_initialized", False)
    monkeypatch.setattr(state_store, "_TELEGRAM_FILE_MIGRATION_CHECKED", True)
    return db_path


def test_remove_telegram_chat_removes_chat_and_rules_only_for_target(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()
    state_store.save_telegram_data(
        {
            "chat_ids": {"111": True, "222": True},
            "filter_rules": {"111": ["alpha"], "222": ["beta"]},
        }
    )

    removed = state_store.remove_telegram_chat(111)
    assert removed is True

    loaded = state_store.load_telegram_data()
    assert loaded["chat_ids"] == {"222": True}
    assert loaded["filter_rules"] == {"222": ["beta"]}


def test_remove_failed_deliveries_for_target_removes_only_selected_target(monkeypatch, tmp_path) -> None:
    _configure_temp_db(monkeypatch, tmp_path)
    storage.init_db()

    state_store.enqueue_failed_delivery(
        channel="telegram",
        target="111",
        payload={"chat_id": 111, "message": "hello"},
        first_delay_seconds=60,
    )
    state_store.enqueue_failed_delivery(
        channel="telegram",
        target="222",
        payload={"chat_id": 222, "message": "world"},
        first_delay_seconds=60,
    )

    removed_count = state_store.remove_failed_deliveries_for_target("telegram", 111)
    assert removed_count == 1

    with storage.get_connection() as conn:
        rows = conn.execute(
            "SELECT channel, target FROM failed_deliveries ORDER BY target"
        ).fetchall()
    assert rows == [("telegram", "222")]
