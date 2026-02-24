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
FAILED_DELIVERIES_CHANNELS = {"telegram", "mastodon"}
MASTODON_PAUSE_CONSUMERS = {"mastodon_bot", "mastodon_control_bot"}

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


def enqueue_failed_delivery(
    *,
    channel: str,
    target: str,
    payload: Dict[str, Any],
    max_retries: int = 3,
    first_delay_seconds: int = 60,
    last_error: str = "",
) -> int | None:
    if channel not in FAILED_DELIVERIES_CHANNELS:
        return None
    storage.init_db()
    now = int(time.time())
    next_retry_at = now + max(1, int(first_delay_seconds))
    retries = max(1, int(max_retries))
    payload_json = json.dumps(payload if isinstance(payload, dict) else {}, ensure_ascii=False)
    with storage.get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO failed_deliveries (
                channel, target, payload_json, attempt_count, max_retries,
                next_retry_at, status, last_error, created_at, updated_at
            ) VALUES (?, ?, ?, 0, ?, ?, 'pending', ?, ?, ?)
            """,
            (channel, str(target), payload_json, retries, next_retry_at, str(last_error or ""), now, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_due_failed_deliveries(
    channel: str,
    *,
    limit: int = 100,
    now_ts: int | None = None,
) -> list[dict]:
    if channel not in FAILED_DELIVERIES_CHANNELS:
        return []
    storage.init_db()
    now = int(now_ts or time.time())
    max_rows = max(1, int(limit))
    with storage.get_connection() as conn:
        rows = conn.execute(
            """
            SELECT id, target, payload_json, attempt_count, max_retries, next_retry_at, last_error, created_at, updated_at
            FROM failed_deliveries
            WHERE channel = ? AND status = 'pending' AND next_retry_at <= ?
            ORDER BY next_retry_at ASC, id ASC
            LIMIT ?
            """,
            (channel, now, max_rows),
        ).fetchall()
    out: list[dict] = []
    for row in rows:
        raw_payload = row[2] or "{}"
        try:
            payload = json.loads(raw_payload)
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append(
            {
                "id": int(row[0]),
                "channel": channel,
                "target": row[1] or "",
                "payload": payload,
                "attempt_count": int(row[3] or 0),
                "max_retries": int(row[4] or 0),
                "next_retry_at": int(row[5] or 0),
                "last_error": row[6] or "",
                "created_at": int(row[7] or 0),
                "updated_at": int(row[8] or 0),
            }
        )
    return out


def schedule_failed_delivery_retry(
    delivery_id: int,
    *,
    attempt_count: int,
    next_retry_at: int,
    last_error: str = "",
):
    storage.init_db()
    now = int(time.time())
    with storage.get_connection() as conn:
        conn.execute(
            """
            UPDATE failed_deliveries
            SET attempt_count = ?, next_retry_at = ?, last_error = ?, status = 'pending', updated_at = ?
            WHERE id = ?
            """,
            (int(attempt_count), int(next_retry_at), str(last_error or ""), now, int(delivery_id)),
        )
        conn.commit()


def mark_failed_delivery_exhausted(
    delivery_id: int,
    *,
    attempt_count: int,
    last_error: str = "",
):
    storage.init_db()
    with storage.get_connection() as conn:
        conn.execute(
            """
            DELETE FROM failed_deliveries
            WHERE id = ?
            """,
            (int(delivery_id),),
        )
        conn.commit()


def remove_failed_delivery(delivery_id: int):
    storage.init_db()
    with storage.get_connection() as conn:
        conn.execute("DELETE FROM failed_deliveries WHERE id = ?", (int(delivery_id),))
        conn.commit()


def prune_failed_deliveries(max_age_days: int = 14):
    storage.init_db()
    now = int(time.time())
    cutoff = now - max(1, int(max_age_days)) * 24 * 60 * 60
    with storage.get_connection() as conn:
        conn.execute(
            """
            DELETE FROM failed_deliveries
            WHERE status = 'exhausted' AND updated_at < ?
            """,
            (cutoff,),
        )
        conn.commit()


def set_mastodon_instance_pause(
    instance_name: str,
    *,
    consumers: List[str] | None = None,
    reporter: str = "",
    pause_seconds: int = 15 * 60,
    reason: str = "",
) -> int:
    requested = consumers if isinstance(consumers, list) and consumers else ["mastodon_bot", "mastodon_control_bot"]
    normalized_consumers = [str(c).strip() for c in requested if str(c).strip() in MASTODON_PAUSE_CONSUMERS]
    if not normalized_consumers:
        return 0
    storage.init_db()
    now = int(time.time())
    pause_until = now + max(1, int(pause_seconds))
    with storage.get_connection() as conn:
        for consumer in normalized_consumers:
            conn.execute(
                """
                INSERT INTO mastodon_instance_pauses_v2 (instance_name, consumer, pause_until, reporter, reason, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(instance_name, consumer) DO UPDATE SET
                    pause_until = excluded.pause_until,
                    reporter = excluded.reporter,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                (
                    str(instance_name),
                    consumer,
                    int(pause_until),
                    str(reporter or ""),
                    str(reason or ""),
                    now,
                ),
            )
        conn.commit()
    return int(pause_until)


def clear_mastodon_instance_pause(instance_name: str, *, consumer: str | None = None):
    storage.init_db()
    with storage.get_connection() as conn:
        if consumer and str(consumer).strip() in MASTODON_PAUSE_CONSUMERS:
            conn.execute(
                "DELETE FROM mastodon_instance_pauses_v2 WHERE instance_name = ? AND consumer = ?",
                (str(instance_name), str(consumer).strip()),
            )
        else:
            conn.execute("DELETE FROM mastodon_instance_pauses_v2 WHERE instance_name = ?", (str(instance_name),))
        conn.commit()


def _cleanup_expired_mastodon_instance_pauses(now_ts: int | None = None):
    storage.init_db()
    now = int(now_ts or time.time())
    with storage.get_connection() as conn:
        conn.execute("DELETE FROM mastodon_instance_pauses_v2 WHERE pause_until <= ?", (now,))
        conn.commit()


def get_mastodon_instance_pause_until(
    instance_name: str,
    *,
    consumer: str,
    now_ts: int | None = None,
) -> int:
    normalized_consumer = str(consumer).strip()
    if normalized_consumer not in MASTODON_PAUSE_CONSUMERS:
        return 0
    storage.init_db()
    now = int(now_ts or time.time())
    _cleanup_expired_mastodon_instance_pauses(now)
    with storage.get_connection() as conn:
        row = conn.execute(
            "SELECT pause_until FROM mastodon_instance_pauses_v2 WHERE instance_name = ? AND consumer = ?",
            (str(instance_name), normalized_consumer),
        ).fetchone()
    if not row:
        return 0
    pause_until = int(row[0] or 0)
    if pause_until <= now:
        clear_mastodon_instance_pause(instance_name, consumer=normalized_consumer)
        return 0
    return pause_until


def get_active_mastodon_instance_pauses(
    *,
    consumer: str | None = None,
    now_ts: int | None = None,
) -> Dict[str, int]:
    storage.init_db()
    now = int(now_ts or time.time())
    _cleanup_expired_mastodon_instance_pauses(now)
    normalized_consumer = str(consumer).strip() if consumer else ""
    with storage.get_connection() as conn:
        if normalized_consumer in MASTODON_PAUSE_CONSUMERS:
            rows = conn.execute(
                """
                SELECT instance_name, pause_until
                FROM mastodon_instance_pauses_v2
                WHERE consumer = ? AND pause_until > ?
                """,
                (normalized_consumer, now),
            ).fetchall()
            return {str(instance): int(until) for instance, until in rows if instance}

        rows = conn.execute(
            """
            SELECT instance_name, MAX(pause_until) as pause_until
            FROM mastodon_instance_pauses_v2
            WHERE pause_until > ?
            GROUP BY instance_name
            """,
            (now,),
        ).fetchall()
    return {str(instance): int(until) for instance, until in rows if instance}


def prune_mastodon_instance_pauses():
    _cleanup_expired_mastodon_instance_pauses()
