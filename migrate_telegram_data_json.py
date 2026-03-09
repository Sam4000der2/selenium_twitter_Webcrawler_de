#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from typing import Any, Dict

from modules import state_store_module as state_store
from modules import storage_module as storage
from modules.paths_module import DATA_FILE as DEFAULT_DATA_FILE


@dataclass(frozen=True)
class MigrationResult:
    status: str
    source_chats: int
    source_rules: int
    db_chats_before: int
    db_rules_before: int
    db_chats_after: int
    db_rules_after: int
    message: str


def _normalize_source_data(raw: Any) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("data.json root must be an object")

    raw_chat_ids = raw.get("chat_ids", {})
    raw_filter_rules = raw.get("filter_rules", {})
    if not isinstance(raw_chat_ids, dict):
        raise ValueError("'chat_ids' must be an object")
    if not isinstance(raw_filter_rules, dict):
        raise ValueError("'filter_rules' must be an object")

    chat_ids: dict[str, bool] = {}
    for chat_id, enabled in raw_chat_ids.items():
        chat_key = str(chat_id).strip()
        if not chat_key:
            continue
        if bool(enabled):
            chat_ids[chat_key] = True

    filter_rules: dict[str, list[str]] = {}
    for chat_id, keywords in raw_filter_rules.items():
        chat_key = str(chat_id).strip()
        if not chat_key:
            continue
        if not isinstance(keywords, list):
            continue
        cleaned_keywords: list[str] = []
        for keyword in keywords:
            kw = str(keyword or "").strip()
            if kw:
                cleaned_keywords.append(kw)
        if cleaned_keywords:
            filter_rules[chat_key] = cleaned_keywords

    return {"chat_ids": chat_ids, "filter_rules": filter_rules}


def _count_rules(data: Dict[str, Any]) -> int:
    rules = data.get("filter_rules", {}) if isinstance(data, dict) else {}
    if not isinstance(rules, dict):
        return 0
    return sum(
        len(keywords)
        for keywords in rules.values()
        if isinstance(keywords, list)
    )


def _configure_db_path(db_path: str | None):
    if not db_path:
        return
    resolved = os.path.abspath(db_path)
    os.environ["NITTER_DB_PATH"] = resolved
    storage.DB_PATH = resolved
    storage._initialized = False
    state_store._TELEGRAM_FILE_MIGRATION_CHECKED = True


def run_migration(
    *,
    data_file: str,
    db_path: str | None = None,
    dry_run: bool = False,
    force: bool = False,
) -> MigrationResult:
    _configure_db_path(db_path)
    # CLI-Migration muss deterministisch nur die explizit angegebene Quelle nutzen.
    state_store._TELEGRAM_FILE_MIGRATION_CHECKED = True

    source_path = os.path.abspath(data_file)
    if not os.path.exists(source_path):
        return MigrationResult(
            status="error",
            source_chats=0,
            source_rules=0,
            db_chats_before=0,
            db_rules_before=0,
            db_chats_after=0,
            db_rules_after=0,
            message=f"Source file not found: {source_path}",
        )

    try:
        with open(source_path, "r", encoding="utf-8") as handle:
            source_raw = json.load(handle)
    except Exception as exc:
        return MigrationResult(
            status="error",
            source_chats=0,
            source_rules=0,
            db_chats_before=0,
            db_rules_before=0,
            db_chats_after=0,
            db_rules_after=0,
            message=f"Failed to read source file: {exc}",
        )

    try:
        source_data = _normalize_source_data(source_raw)
    except ValueError as exc:
        return MigrationResult(
            status="error",
            source_chats=0,
            source_rules=0,
            db_chats_before=0,
            db_rules_before=0,
            db_chats_after=0,
            db_rules_after=0,
            message=f"Invalid source structure: {exc}",
        )

    source_chats = len(source_data["chat_ids"])
    source_rules = _count_rules(source_data)

    db_before = state_store.load_telegram_data()
    db_chats_before = len(db_before.get("chat_ids", {}))
    db_rules_before = _count_rules(db_before)

    if (db_chats_before > 0 or db_rules_before > 0) and not force:
        return MigrationResult(
            status="skipped",
            source_chats=source_chats,
            source_rules=source_rules,
            db_chats_before=db_chats_before,
            db_rules_before=db_rules_before,
            db_chats_after=db_chats_before,
            db_rules_after=db_rules_before,
            message="DB already contains Telegram data. Use --force to overwrite.",
        )

    if dry_run:
        return MigrationResult(
            status="dry_run",
            source_chats=source_chats,
            source_rules=source_rules,
            db_chats_before=db_chats_before,
            db_rules_before=db_rules_before,
            db_chats_after=db_chats_before,
            db_rules_after=db_rules_before,
            message="Dry-run complete. No changes written.",
        )

    state_store.save_telegram_data(source_data)
    db_after = state_store.load_telegram_data()
    db_chats_after = len(db_after.get("chat_ids", {}))
    db_rules_after = _count_rules(db_after)

    return MigrationResult(
        status="migrated",
        source_chats=source_chats,
        source_rules=source_rules,
        db_chats_before=db_chats_before,
        db_rules_before=db_rules_before,
        db_chats_after=db_chats_after,
        db_rules_after=db_rules_after,
        message="Migration complete.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Migrate Telegram chat/filter state from data.json into nitter_bot.db.",
    )
    parser.add_argument(
        "--data-file",
        default=DEFAULT_DATA_FILE,
        help=f"Path to source data.json (default: {DEFAULT_DATA_FILE})",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Path to nitter_bot.db (overrides NITTER_DB_PATH for this run).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and show counts without writing to DB.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing Telegram data in DB.",
    )
    return parser


def _print_result(result: MigrationResult):
    print(f"status: {result.status}")
    print(result.message)
    print(f"source_chats: {result.source_chats}")
    print(f"source_rules: {result.source_rules}")
    print(f"db_chats_before: {result.db_chats_before}")
    print(f"db_rules_before: {result.db_rules_before}")
    print(f"db_chats_after: {result.db_chats_after}")
    print(f"db_rules_after: {result.db_rules_after}")


def main() -> int:
    args = _build_parser().parse_args()
    result = run_migration(
        data_file=args.data_file,
        db_path=args.db_path,
        dry_run=args.dry_run,
        force=args.force,
    )
    _print_result(result)
    return 0 if result.status in {"migrated", "dry_run", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
