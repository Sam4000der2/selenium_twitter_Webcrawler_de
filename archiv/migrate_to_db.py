#!/usr/bin/env python3
"""
Migration helper: liest alte TXT/CSV/JSON/SQLite-Dateien und schreibt sie
in die gemeinsame SQLite-DB (nitter_bot.db). Standardpfade verweisen auf
/home/sascha/bots, können aber per Argument überschrieben werden.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Dict, List

import state_store
import storage

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("migrate_to_db")


def read_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning(f"Überspringe {path} (kein gültiges JSON): {exc}")
        return None


def migrate_data_json(base_dir: Path):
    path = base_dir / "data.json"
    if not path.exists():
        return 0
    data = read_json(path)
    if not isinstance(data, dict):
        return 0
    state_store.save_telegram_data(data)
    return 1


def migrate_mastodon_rules(base_dir: Path):
    path = base_dir / "mastodon_rules.json"
    if not path.exists():
        return 0
    data = read_json(path)
    if not isinstance(data, dict):
        return 0
    state_store.save_mastodon_rules(data)
    return 1


def migrate_mastodon_versions(base_dir: Path):
    path = base_dir / "mastodon_versions.json"
    if not path.exists():
        return 0
    data = read_json(path)
    if not isinstance(data, dict):
        return 0
    state_store.save_mastodon_versions(data)
    return 1


def migrate_existing_tweets(base_dir: Path):
    path = base_dir / "existing_tweets.txt"
    if not path.exists():
        return 0
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        if lines:
            state_store.save_twitter_history(lines)
        return len(lines)
    except Exception as exc:
        logger.warning(f"Konnte existing_tweets.txt nicht lesen: {exc}")
        return 0


def migrate_nitter_history(base_dir: Path):
    path = base_dir / "nitter_existing_tweets.txt"
    if not path.exists():
        return 0

    history_map: Dict[str, List[str]] = {}
    try:
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            return 0
        stripped = content.lstrip()
        if stripped.startswith("{"):
            data = json.loads(content)
            if isinstance(data, dict):
                for user, entries in data.items():
                    if isinstance(entries, list):
                        cleaned = [(e or "").strip() for e in entries if isinstance(e, str) and (e or "").strip()]
                        if cleaned:
                            history_map[str(user)] = cleaned
        else:
            legacy = [(line or "").strip() for line in content.splitlines() if line.strip()]
            if legacy:
                history_map["_legacy"] = legacy
    except Exception as exc:
        logger.warning(f"Konnte nitter_existing_tweets.txt nicht lesen: {exc}")
        return 0

    state_store.save_nitter_history(history_map)
    return sum(len(v) for v in history_map.values())


def migrate_nitter_users(base_dir: Path):
    path = base_dir / "nitter_users.csv"
    if not path.exists():
        return 0
    users: Dict[str, Dict[str, str]] = {}
    try:
        with path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                username = (row.get("username") or "").strip()
                if not username:
                    continue
                users[username] = {
                    "interval_seconds": row.get("interval_seconds") or row.get("interval") or "",
                    "active_start": row.get("active_start") or "",
                    "active_end": row.get("active_end") or "",
                }
    except Exception as exc:
        logger.warning(f"Konnte nitter_users.csv nicht lesen: {exc}")
        return 0

    state_store.save_nitter_users(users)
    return len(users)


def migrate_bsky_viz(base_dir: Path):
    path = base_dir / "viz_berlin_entries.txt"
    if not path.exists():
        return 0
    entries: List[str] = []
    try:
        content = path.read_text(encoding="utf-8").strip()
        if content.startswith("["):
            data = json.loads(content)
            if isinstance(data, list):
                entries = [str(e).strip() for e in data if str(e).strip()]
        else:
            entries = [ln.strip() for ln in content.splitlines() if ln.strip()]
    except Exception as exc:
        logger.warning(f"Konnte viz_berlin_entries.txt nicht lesen: {exc}")
        return 0

    if entries:
        state_store.save_bsky_entries("viz_berlin", entries)
    return len(entries)


def migrate_gemini_models(base_dir: Path):
    path = base_dir / "gemini_models.csv"
    if not path.exists():
        return 0

    statuses: Dict[str, Dict[str, str]] = {}
    models: List[str] = []
    last_refresh = ""
    try:
        with path.open("r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                name = (row.get("name") or "").strip()
                if not name:
                    continue
                if name == "__meta__":
                    last_refresh = row.get("last_update") or ""
                    continue
                status = (row.get("status") or "ok").strip()
                last_update = row.get("last_update") or ""
                last_error = row.get("last_error") or ""
                statuses[name] = {
                    "status": status,
                    "last_update": last_update,
                    "last_error": last_error,
                }
                models.append(name)
    except Exception as exc:
        logger.warning(f"Konnte gemini_models.csv nicht lesen: {exc}")
        return 0

    payload = {
        "last_refresh": last_refresh,
        "statuses": statuses,
        "models": models,
    }
    state_store.save_gemini_cache(payload)
    return len(models)


def migrate_mastodon_posts_db(base_dir: Path):
    path = base_dir / "mastodon_posts.db"
    if not path.exists():
        return 0
    if not path.is_file():
        return 0

    count = 0
    try:
        conn = sqlite3.connect(path)
        cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='posts'")
        if not cur.fetchone():
            return 0
        cur = conn.execute("PRAGMA table_info(posts)")
        cols = {row[1] for row in cur.fetchall()}
        has_created = "created_at" in cols
        query = "SELECT instance, status_id, url" + (", created_at" if has_created else "") + " FROM posts"
        for row in conn.execute(query):
            instance, status_id, url = row[:3]
            created_at = int(row[3]) if has_created and row[3] is not None else None
            key = f"{instance}:{status_id}"
            storage.write_value(
                "mastodon_posts",
                key,
                {"instance": instance, "status_id": status_id, "url": url},
                created_at=created_at,
            )
            count += 1
    except Exception as exc:
        logger.warning(f"Konnte mastodon_posts.db nicht migrieren: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return count


def run_all(base_dir: Path):
    base_dir = base_dir.resolve()
    logger.info(f"Migriere aus {base_dir}")
    storage.init_db()

    report = {}
    report["telegram_data"] = migrate_data_json(base_dir)
    report["mastodon_rules"] = migrate_mastodon_rules(base_dir)
    report["mastodon_versions"] = migrate_mastodon_versions(base_dir)
    report["existing_tweets"] = migrate_existing_tweets(base_dir)
    report["nitter_history_entries"] = migrate_nitter_history(base_dir)
    report["nitter_users"] = migrate_nitter_users(base_dir)
    report["bsky_viz_entries"] = migrate_bsky_viz(base_dir)
    report["gemini_models"] = migrate_gemini_models(base_dir)
    report["mastodon_posts"] = migrate_mastodon_posts_db(base_dir)

    logger.info("Migration abgeschlossen:")
    for key, val in report.items():
        logger.info(f"  {key}: {val}")


def parse_args():
    parser = argparse.ArgumentParser(description="Migriert alte Dateien in die gemeinsame SQLite-DB.")
    parser.add_argument(
        "--base-dir",
        default=str(Path(__file__).resolve().parent),
        help="Verzeichnis mit den bisherigen Dateien (default: Skript-Ordner)",
    )
    parser.add_argument(
        "--db-path",
        help="Pfad zur Zieldatenbank (setzt NITTER_DB_PATH, default Skript-Ordner/nitter_bot.db)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.db_path:
        os.environ["NITTER_DB_PATH"] = args.db_path
    elif "NITTER_DB_PATH" not in os.environ:
        os.environ["NITTER_DB_PATH"] = str(Path(__file__).resolve().parent / "nitter_bot.db")
    run_all(Path(args.base_dir))
