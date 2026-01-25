import json
import logging
import os
import sqlite3
import time
from typing import Any, Dict, Iterable, Tuple

DB_PATH = (
    os.environ.get("NITTER_DB_PATH")
    or os.environ.get("MASTODON_POST_DB")
    or "/home/sascha/bots/nitter_bot.db"
)

_initialized = False


def _now() -> int:
    return int(time.time())


def get_connection() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    return sqlite3.connect(DB_PATH)


def init_db():
    global _initialized
    if _initialized:
        return
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS telegram_filters (
                chat_id INTEGER PRIMARY KEY,
                keywords TEXT NOT NULL DEFAULT '[]',
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mastodon_rules (
                acct TEXT PRIMARY KEY,
                rules_json TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mastodon_versions (
                instance TEXT PRIMARY KEY,
                version TEXT,
                checked_at INTEGER,
                quote_policy TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gemini_models (
                name TEXT PRIMARY KEY,
                status TEXT,
                last_update TEXT,
                last_error TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS gemini_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS twitter_history (
                url TEXT PRIMARY KEY,
                created_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nitter_history (
                username TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (username, url)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS nitter_users (
                username TEXT PRIMARY KEY,
                interval_seconds INTEGER,
                active_start TEXT,
                active_end TEXT,
                updated_at INTEGER NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bsky_history (
                feed_name TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (feed_name, url)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs_live (
                ts INTEGER NOT NULL,
                line TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS logs_archive (
                ts INTEGER NOT NULL,
                line TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS mastodon_posts (
                instance TEXT NOT NULL,
                status_id TEXT NOT NULL,
                url TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                PRIMARY KEY (instance, status_id)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_masto_created ON mastodon_posts(created_at)")
        conn.commit()
    _initialized = True


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(raw: str, default: Any):
    try:
        return json.loads(raw)
    except Exception:
        return default


# Telegram
def _read_telegram() -> Dict[str, Any]:
    with get_connection() as conn:
        rows = conn.execute("SELECT chat_id, keywords FROM telegram_filters").fetchall()
    chat_ids = {}
    filter_rules = {}
    for chat_id, keywords_raw in rows:
        try:
            keywords = _json_loads(keywords_raw, [])
        except Exception:
            keywords = []
        chat_ids[str(chat_id)] = chat_id
        filter_rules[str(chat_id)] = keywords if isinstance(keywords, list) else []
    return {"chat_ids": chat_ids, "filter_rules": filter_rules}


def _write_telegram(data: Dict[str, Any]):
    chat_ids = data.get("chat_ids", {}) if isinstance(data, dict) else {}
    filter_rules = data.get("filter_rules", {}) if isinstance(data, dict) else {}
    ts = _now()
    with get_connection() as conn:
        conn.execute("DELETE FROM telegram_filters")
        for chat_id_str, cid in chat_ids.items():
            chat_id = int(cid)
            keywords = filter_rules.get(str(chat_id_str), [])
            conn.execute(
                "INSERT OR REPLACE INTO telegram_filters (chat_id, keywords, updated_at) VALUES (?, ?, ?)",
                (chat_id, _json_dumps(keywords), ts),
            )
        conn.commit()


# Mastodon rules / versions
def _read_mastodon_rules():
    with get_connection() as conn:
        rows = conn.execute("SELECT acct, rules_json FROM mastodon_rules").fetchall()
    users = {}
    for acct, raw in rows:
        users[acct] = _json_loads(raw, {})
    return {"users": users}


def _write_mastodon_rules(data: Dict[str, Any]):
    users = data.get("users", {}) if isinstance(data, dict) else {}
    ts = _now()
    with get_connection() as conn:
        conn.execute("DELETE FROM mastodon_rules")
        for acct, rules in users.items():
            conn.execute(
                "INSERT OR REPLACE INTO mastodon_rules (acct, rules_json, updated_at) VALUES (?, ?, ?)",
                (acct, _json_dumps(rules), ts),
            )
        conn.commit()


def _read_mastodon_versions():
    with get_connection() as conn:
        rows = conn.execute("SELECT instance, version, checked_at, quote_policy FROM mastodon_versions").fetchall()
    out = {}
    for instance, version, checked_at, quote_policy in rows:
        out[instance] = {"version": version, "checked_at": checked_at, "quote_policy": quote_policy}
    return out


def _write_mastodon_versions(data: Dict[str, Any]):
    if not isinstance(data, dict):
        return
    with get_connection() as conn:
        conn.execute("DELETE FROM mastodon_versions")
        for inst, payload in data.items():
            if not isinstance(payload, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO mastodon_versions (instance, version, checked_at, quote_policy) VALUES (?, ?, ?, ?)",
                (
                    inst,
                    payload.get("version"),
                    payload.get("checked_at"),
                    payload.get("quote_policy"),
                ),
            )
        conn.commit()


# Gemini cache
def _read_gemini_cache():
    with get_connection() as conn:
        statuses_rows = conn.execute(
            "SELECT name, status, last_update, last_error FROM gemini_models"
        ).fetchall()
        meta_rows = conn.execute("SELECT key, value FROM gemini_meta").fetchall()
    statuses = {}
    models = []
    for name, status, last_update, last_error in statuses_rows:
        statuses[name] = {
            "status": status or "ok",
            "last_update": last_update or "",
            "last_error": last_error or "",
        }
        models.append(name)
    meta = {k: v for k, v in meta_rows}
    last_refresh = meta.get("last_refresh", "")
    order_raw = meta.get("model_order", "")
    try:
        order_list = json.loads(order_raw) if order_raw else []
        if isinstance(order_list, list):
            models = order_list
    except Exception:
        pass
    return {"last_refresh": last_refresh, "statuses": statuses, "models": models}


def _write_gemini_cache(cache: Dict[str, Any]):
    statuses = cache.get("statuses", {}) if isinstance(cache, dict) else {}
    models = cache.get("models", []) if isinstance(cache, dict) else []
    last_refresh = cache.get("last_refresh", "") if isinstance(cache, dict) else ""
    with get_connection() as conn:
        conn.execute("DELETE FROM gemini_models")
        for name, st in statuses.items():
            if not isinstance(st, dict):
                continue
            conn.execute(
                "INSERT OR REPLACE INTO gemini_models (name, status, last_update, last_error) VALUES (?, ?, ?, ?)",
                (
                    name,
                    st.get("status", "ok"),
                    st.get("last_update", ""),
                    st.get("last_error", ""),
                ),
            )
        conn.execute("DELETE FROM gemini_meta")
        conn.execute(
            "INSERT OR REPLACE INTO gemini_meta (key, value) VALUES (?, ?)",
            ("last_refresh", last_refresh or ""),
        )
        conn.execute(
            "INSERT OR REPLACE INTO gemini_meta (key, value) VALUES (?, ?)",
            ("model_order", _json_dumps(models)),
        )
        conn.commit()


# Twitter history
def _read_twitter_history():
    with get_connection() as conn:
        rows = conn.execute("SELECT url FROM twitter_history ORDER BY created_at").fetchall()
    return [r[0] for r in rows]


def _write_twitter_history(urls: Iterable[str]):
    urls = [u for u in urls or [] if u]
    ts = _now()
    with get_connection() as conn:
        conn.execute("DELETE FROM twitter_history")
        for idx, url in enumerate(dict.fromkeys(urls)):
            conn.execute(
                "INSERT OR REPLACE INTO twitter_history (url, created_at) VALUES (?, ?)",
                (url, ts + idx),
            )
        conn.commit()


# Nitter history and users
def _read_nitter_history():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT username, url FROM nitter_history ORDER BY created_at"
        ).fetchall()
    out: Dict[str, list] = {}
    for user, url in rows:
        out.setdefault(user, []).append(url)
    return out


def _write_nitter_history(history: Dict[str, Any]):
    ts_base = _now()
    with get_connection() as conn:
        conn.execute("DELETE FROM nitter_history")
        for user, urls in (history or {}).items():
            if not isinstance(urls, list):
                continue
            for idx, url in enumerate(dict.fromkeys([u for u in urls if u])):
                conn.execute(
                    "INSERT OR REPLACE INTO nitter_history (username, url, created_at) VALUES (?, ?, ?)",
                    (user, url, ts_base + idx),
                )
        conn.commit()


def _read_nitter_users():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT username, interval_seconds, active_start, active_end FROM nitter_users"
        ).fetchall()
    out = {}
    for username, interval_seconds, active_start, active_end in rows:
        out[username] = {
            "interval_seconds": interval_seconds,
            "active_start": active_start or "",
            "active_end": active_end or "",
        }
    return out


def _write_nitter_users(users: Dict[str, Any]):
    ts = _now()
    with get_connection() as conn:
        conn.execute("DELETE FROM nitter_users")
        for username, cfg in (users or {}).items():
            if not isinstance(cfg, dict):
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO nitter_users (username, interval_seconds, active_start, active_end, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    username,
                    cfg.get("interval_seconds"),
                    cfg.get("active_start", ""),
                    cfg.get("active_end", ""),
                    ts,
                ),
            )
        conn.commit()


# Bluesky history
def _read_bsky_history(feed_name: str):
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT url FROM bsky_history WHERE feed_name = ? ORDER BY created_at", (feed_name,)
        ).fetchall()
    return [r[0] for r in rows]


def _write_bsky_history(feed_name: str, urls: Iterable[str]):
    urls = [u for u in urls or [] if u]
    ts = _now()
    with get_connection() as conn:
        conn.execute("DELETE FROM bsky_history WHERE feed_name = ?", (feed_name,))
        for idx, url in enumerate(dict.fromkeys(urls)):
            conn.execute(
                "INSERT OR REPLACE INTO bsky_history (feed_name, url, created_at) VALUES (?, ?, ?)",
                (feed_name, url, ts + idx),
            )
        conn.commit()


# Logs
def _read_logs(table: str):
    with get_connection() as conn:
        rows = conn.execute(f"SELECT ts, line FROM {table} ORDER BY ts").fetchall()
    return [{"ts": ts, "line": line} for ts, line in rows]


def _write_logs(table: str, entries: Iterable[dict]):
    with get_connection() as conn:
        conn.execute(f"DELETE FROM {table}")
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            ts = entry.get("ts")
            line = entry.get("line")
            if ts is None or not line:
                continue
            conn.execute(f"INSERT INTO {table} (ts, line) VALUES (?, ?)", (int(ts), str(line)))
        conn.commit()


def _append_logs(table: str, entries: Iterable[dict], cutoff_days: int):
    now = _now()
    cutoff = now - max(0, cutoff_days) * 24 * 60 * 60
    existing = _read_logs(table)
    combined = existing + [e for e in entries or [] if isinstance(e, dict)]
    pruned = []
    seen: set[Tuple[int, str]] = set()
    for e in combined:
        ts = int(e.get("ts", 0))
        line = str(e.get("line") or "")
        if not line or ts < cutoff:
            continue
        key = (ts, line)
        if key in seen:
            continue
        seen.add(key)
        pruned.append({"ts": ts, "line": line})
    _write_logs(table, pruned)
    return pruned


# Mastodon posts
def _read_mastodon_post(key: str):
    try:
        instance, status_id = key.split(":", 1)
    except Exception:
        return None
    with get_connection() as conn:
        cur = conn.execute(
            "SELECT url FROM mastodon_posts WHERE instance = ? AND status_id = ?",
            (instance, status_id),
        )
        row = cur.fetchone()
    return {"url": row[0]} if row else None


def _write_mastodon_post(key: str, value: dict, created_at: int | None = None):
    try:
        instance, status_id = key.split(":", 1)
    except Exception:
        return
    url = value.get("url") if isinstance(value, dict) else None
    if not url:
        return
    ts = int(created_at or _now())
    with get_connection() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO mastodon_posts (instance, status_id, url, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (instance, status_id, url, ts),
        )
        conn.commit()


# Public API (compat)
def write_value(bucket: str, key: str, value: Any, created_at: float | int | None = None):
    init_db()
    if bucket == "telegram_config":
        return _write_telegram(value)
    if bucket == "mastodon_rules":
        return _write_mastodon_rules(value)
    if bucket == "mastodon_versions":
        return _write_mastodon_versions(value)
    if bucket == "gemini_models":
        return _write_gemini_cache(value)
    if bucket == "twitter_history":
        return _write_twitter_history(value if isinstance(value, list) else [])
    if bucket == "nitter_history":
        return _write_nitter_history(value if isinstance(value, dict) else {})
    if bucket == "nitter_users":
        return _write_nitter_users(value if isinstance(value, dict) else {})
    if bucket == "bsky_feed_history":
        return _write_bsky_history(key, value if isinstance(value, list) else [])
    if bucket in {"logs_live", "logs_archive"}:
        return _write_logs(bucket, value if isinstance(value, list) else [])
    if bucket == "mastodon_posts":
        return _write_mastodon_post(key, value if isinstance(value, dict) else {}, created_at)


def read_value(bucket: str, key: str, default: Any = None):
    init_db()
    if bucket == "telegram_config":
        return _read_telegram()
    if bucket == "mastodon_rules":
        return _read_mastodon_rules()
    if bucket == "mastodon_versions":
        return _read_mastodon_versions()
    if bucket == "gemini_models":
        return _read_gemini_cache()
    if bucket == "twitter_history":
        return _read_twitter_history()
    if bucket == "nitter_history":
        return _read_nitter_history()
    if bucket == "nitter_users":
        return _read_nitter_users()
    if bucket == "bsky_feed_history":
        return _read_bsky_history(key)
    if bucket in {"logs_live", "logs_archive"}:
        return _read_logs(bucket)
    if bucket == "mastodon_posts":
        return _read_mastodon_post(key) or default
    return default


def get_bucket(bucket: str) -> Dict[str, Any]:
    if bucket in {
        "nitter_history",
        "nitter_users",
        "mastodon_rules",
        "mastodon_versions",
        "gemini_models",
        "telegram_config",
    }:
        return read_value(bucket, "", {})
    return {}


def replace_bucket(bucket: str, mapping: Dict[str, Any]):
    if bucket == "nitter_history":
        return _write_nitter_history(mapping)
    if bucket == "nitter_users":
        return _write_nitter_users(mapping)


def read_list(bucket: str, key: str) -> list:
    data = read_value(bucket, key, [])
    return data if isinstance(data, list) else []


def write_list(bucket: str, key: str, items: list, limit: int | None = None):
    cleaned = [i for i in (items or []) if i is not None]
    if limit is not None and limit > 0:
        cleaned = cleaned[-limit:]
    write_value(bucket, key, cleaned)
    return cleaned


def append_to_list(bucket: str, key: str, new_items: list, limit: int | None = None) -> list:
    if bucket == "twitter_history":
        existing = _read_twitter_history()
        combined = existing + [n for n in (new_items or []) if n]
        return write_list(bucket, key, combined, limit)
    if bucket == "bsky_feed_history":
        existing = _read_bsky_history(key)
        combined = existing + [n for n in (new_items or []) if n]
        return write_list(bucket, key, combined, limit)
    return read_list(bucket, key)


def prune_bucket_before(bucket: str, cutoff_ts: float | int):
    if bucket == "mastodon_posts":
        with get_connection() as conn:
            conn.execute("DELETE FROM mastodon_posts WHERE created_at < ?", (int(cutoff_ts),))
            conn.commit()
    elif bucket in {"logs_live", "logs_archive"}:
        with get_connection() as conn:
            conn.execute(f"DELETE FROM {bucket} WHERE ts < ?", (int(cutoff_ts),))
            conn.commit()


def delete_entry(bucket: str, key: str):
    if bucket == "twitter_history":
        with get_connection() as conn:
            conn.execute("DELETE FROM twitter_history WHERE url = ?", (key,))
            conn.commit()
    elif bucket == "nitter_users":
        with get_connection() as conn:
            conn.execute("DELETE FROM nitter_users WHERE username = ?", (key,))
            conn.commit()
    elif bucket == "nitter_history":
        try:
            username, url = key.split(":", 1)
        except Exception:
            return
        with get_connection() as conn:
            conn.execute("DELETE FROM nitter_history WHERE username = ? AND url = ?", (username, url))
            conn.commit()
