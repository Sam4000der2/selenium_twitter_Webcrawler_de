from __future__ import annotations

import json
import time
from typing import Any, Dict, Iterable, List

import storage

TELEGRAM_BUCKET = "telegram_config"
TELEGRAM_KEY = "chat_config"

MASTODON_RULES_BUCKET = "mastodon_rules"
MASTODON_RULES_KEY = "rules"

MASTODON_VERSIONS_BUCKET = "mastodon_versions"
MASTODON_VERSIONS_KEY = "versions"

GEMINI_CACHE_BUCKET = "gemini_models"
GEMINI_CACHE_KEY = "cache"

BROADCAST_HISTORY_BUCKET = "twitter_history"
BROADCAST_HISTORY_KEY = "existing_tweets"

NITTER_HISTORY_BUCKET = "nitter_history"
NITTER_USERS_BUCKET = "nitter_users"

BSKY_FEED_BUCKET = "bsky_feed_history"
LOG_LIVE_BUCKET = "logs_live"
LOG_ARCHIVE_BUCKET = "logs_archive"

LIVE_LOG_RETENTION_DAYS = 7
ARCHIVE_LOG_RETENTION_DAYS = 90


def load_telegram_data() -> Dict[str, Any]:
    data = storage.read_value(TELEGRAM_BUCKET, TELEGRAM_KEY, {"chat_ids": {}, "filter_rules": {}})
    if not isinstance(data, dict):
        return {"chat_ids": {}, "filter_rules": {}}
    data.setdefault("chat_ids", {})
    data.setdefault("filter_rules", {})
    return data


def save_telegram_data(data: Dict[str, Any]):
    cleaned = data if isinstance(data, dict) else {"chat_ids": {}, "filter_rules": {}}
    cleaned.setdefault("chat_ids", {})
    cleaned.setdefault("filter_rules", {})
    storage.write_value(TELEGRAM_BUCKET, TELEGRAM_KEY, cleaned)


def load_mastodon_rules() -> Dict[str, Any]:
    data = storage.read_value(MASTODON_RULES_BUCKET, MASTODON_RULES_KEY, {"users": {}})
    if not isinstance(data, dict):
        return {"users": {}}
    data.setdefault("users", {})
    return data


def save_mastodon_rules(rules: Dict[str, Any]):
    payload = rules if isinstance(rules, dict) else {"users": {}}
    payload.setdefault("users", {})
    storage.write_value(MASTODON_RULES_BUCKET, MASTODON_RULES_KEY, payload)


def load_mastodon_versions() -> Dict[str, Any]:
    data = storage.read_value(MASTODON_VERSIONS_BUCKET, MASTODON_VERSIONS_KEY, {})
    return data if isinstance(data, dict) else {}


def save_mastodon_versions(data: Dict[str, Any]):
    storage.write_value(MASTODON_VERSIONS_BUCKET, MASTODON_VERSIONS_KEY, data if isinstance(data, dict) else {})


def load_gemini_cache() -> Dict[str, Any]:
    data = storage.read_value(GEMINI_CACHE_BUCKET, GEMINI_CACHE_KEY, {})
    return data if isinstance(data, dict) else {}


def save_gemini_cache(cache: Dict[str, Any]):
    storage.write_value(GEMINI_CACHE_BUCKET, GEMINI_CACHE_KEY, cache if isinstance(cache, dict) else {})


def load_twitter_history() -> List[str]:
    data = storage.read_value(BROADCAST_HISTORY_BUCKET, BROADCAST_HISTORY_KEY, [])
    return data if isinstance(data, list) else []


def save_twitter_history(entries: List[str], limit: int | None = None):
    if entries is None:
        entries = []
    cleaned = [e for e in entries if e]
    if limit is not None and limit > 0:
        cleaned = cleaned[-limit:]
    storage.write_value(BROADCAST_HISTORY_BUCKET, BROADCAST_HISTORY_KEY, cleaned)


def get_twitter_history_entries() -> list[dict]:
    storage.init_db()
    with storage.get_connection() as conn:
        rows = conn.execute("SELECT url, created_at FROM twitter_history ORDER BY created_at").fetchall()
    return [{"url": url, "created_at": created_at} for url, created_at in rows]


def load_nitter_history() -> Dict[str, Any]:
    data = storage.get_bucket(NITTER_HISTORY_BUCKET)
    return data if isinstance(data, dict) else {}


def save_nitter_history(history_map: Dict[str, Any]):
    storage.replace_bucket(NITTER_HISTORY_BUCKET, history_map if isinstance(history_map, dict) else {})


def get_nitter_history_entries(username: str | None = None) -> list[dict]:
    storage.init_db()
    params = ()
    sql = "SELECT username, url, created_at FROM nitter_history"
    if username:
        sql += " WHERE username = ?"
        params = (username,)
    sql += " ORDER BY created_at"
    with storage.get_connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{"username": u, "url": url, "created_at": created_at} for u, url, created_at in rows]


def load_nitter_users() -> Dict[str, Any]:
    data = storage.get_bucket(NITTER_USERS_BUCKET)
    return data if isinstance(data, dict) else {}


def save_nitter_users(user_map: Dict[str, Any]):
    storage.replace_bucket(NITTER_USERS_BUCKET, user_map if isinstance(user_map, dict) else {})


def load_bsky_entries(feed_name: str) -> List[str]:
    data = storage.read_value(BSKY_FEED_BUCKET, feed_name, [])
    return data if isinstance(data, list) else []


def save_bsky_entries(feed_name: str, entries: List[str], limit: int | None = None):
    if entries is None:
        entries = []
    cleaned = [e for e in entries if e]
    if limit is not None and limit > 0:
        cleaned = cleaned[-limit:]
    storage.write_value(BSKY_FEED_BUCKET, feed_name, cleaned)


def get_bsky_feed_names() -> list[str]:
    storage.init_db()
    with storage.get_connection() as conn:
        rows = conn.execute("SELECT DISTINCT feed_name FROM bsky_history").fetchall()
    return [r[0] for r in rows if r and r[0]]


def _normalize_log_entries(entries: Iterable[dict]) -> list[dict]:
    normalized: list[dict] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        ts_raw = entry.get("ts")
        line = (entry.get("line") or "").strip()
        if not line:
            continue
        try:
            ts_int = int(ts_raw)
        except Exception:
            continue
        normalized.append({"ts": ts_int, "line": line})
    return normalized


def _dedupe_log_entries(entries: list[dict]) -> list[dict]:
    seen: set[tuple[int, str]] = set()
    deduped: list[dict] = []
    for entry in entries:
        key = (entry.get("ts"), entry.get("line"))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _store_logs(bucket: str, entries: Iterable[dict], retention_days: int) -> list[dict]:
    now = int(time.time())
    cutoff = now - max(0, retention_days) * 24 * 60 * 60
    existing = storage.read_value(bucket, "entries", [])
    existing_list = existing if isinstance(existing, list) else []
    normalized_new = _normalize_log_entries(entries)
    combined = existing_list + normalized_new
    pruned = [
        e
        for e in combined
        if isinstance(e, dict) and isinstance(e.get("ts"), int) and e.get("ts") >= cutoff
    ]
    pruned = _dedupe_log_entries(pruned)
    storage.write_value(bucket, "entries", pruned)
    return pruned


def store_live_logs(entries: Iterable[dict]) -> list[dict]:
    return _store_logs(LOG_LIVE_BUCKET, entries, LIVE_LOG_RETENTION_DAYS)


def store_archive_logs(entries: Iterable[dict]) -> list[dict]:
    return _store_logs(LOG_ARCHIVE_BUCKET, entries, ARCHIVE_LOG_RETENTION_DAYS)


def prune_logs():
    _store_logs(LOG_LIVE_BUCKET, [], LIVE_LOG_RETENTION_DAYS)
    _store_logs(LOG_ARCHIVE_BUCKET, [], ARCHIVE_LOG_RETENTION_DAYS)
