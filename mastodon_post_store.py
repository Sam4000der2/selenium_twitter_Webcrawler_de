import logging
import time
from typing import Optional

import storage

RETENTION_SECONDS = 7 * 24 * 60 * 60
BUCKET = "mastodon_posts"


def init_db():
    storage.init_db()


def prune_expired(retention_seconds: int = RETENTION_SECONDS):
    cutoff = int(time.time() - retention_seconds)
    storage.prune_bucket_before(BUCKET, cutoff)


def store_post(instance: str, status_id: str, url: str, created_at_ts: int | float | None = None):
    if not instance or not status_id or not url:
        return
    ts = int(created_at_ts or time.time())
    key = f"{instance}:{status_id}"
    storage.write_value(
        BUCKET,
        key,
        {"instance": instance, "status_id": status_id, "url": url},
        created_at=ts,
    )


def get_post(instance: str, status_id: str) -> Optional[str]:
    if not instance or not status_id:
        return None
    key = f"{instance}:{status_id}"
    data = storage.read_value(BUCKET, key, None)
    try:
        return data.get("url") if isinstance(data, dict) else None
    except Exception as exc:
        logging.error(f"mastodon_post_store: Fehler beim Lesen von {instance}/{status_id}: {exc}")
        return None
