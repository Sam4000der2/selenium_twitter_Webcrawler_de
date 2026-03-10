from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import TextIO

try:
    import fcntl
except ImportError:  # pragma: no cover - nur relevant auf Non-POSIX-Plattformen
    fcntl = None

from modules.paths_module import BASE_DIR


LOCK_DIR_ENV = "BOTS_RUNTIME_LOCK_DIR"


def _resolve_lock_path(group_name: str) -> Path:
    requested = (os.environ.get(LOCK_DIR_ENV) or "").strip()
    if requested:
        lock_dir = Path(requested).expanduser()
    else:
        lock_dir = BASE_DIR / "runtime"
    lock_dir.mkdir(parents=True, exist_ok=True)
    safe_group = "".join(ch for ch in str(group_name or "").strip() if ch.isalnum() or ch in ("-", "_"))
    if not safe_group:
        safe_group = "default"
    return lock_dir / f"{safe_group}.sender.lock"


def _read_lock_owner(lock_path: Path) -> str:
    try:
        raw = lock_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not raw:
        return ""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    if not isinstance(payload, dict):
        return ""
    bot = str(payload.get("bot") or "").strip()
    pid = str(payload.get("pid") or "").strip()
    host = str(payload.get("host") or "").strip()
    parts = [part for part in (bot, f"pid={pid}" if pid else "", host) if part]
    return ", ".join(parts)


def try_acquire_sender_lock(group_name: str, bot_name: str) -> tuple[bool, str, TextIO | None]:
    """
    Versucht exklusiv den Sender-Lock für eine Varianten-Gruppe zu halten.
    Rückgabe: (can_send, reason, lock_handle)
    """
    try:
        lock_path = _resolve_lock_path(group_name)
    except OSError as exc:
        return False, f"Lock-Pfad konnte nicht vorbereitet werden: {exc}", None

    if fcntl is None:
        return True, "locking nicht verfügbar (fcntl fehlt)", None

    try:
        handle = lock_path.open("a+", encoding="utf-8")
    except OSError as exc:
        return False, f"Lock-Datei konnte nicht geöffnet werden: {exc}", None

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        owner = _read_lock_owner(lock_path)
        handle.close()
        reason = f"Sender-Lock belegt ({owner})" if owner else "Sender-Lock belegt"
        return False, reason, None
    except OSError as exc:
        handle.close()
        return False, f"Sender-Lock konnte nicht gesetzt werden: {exc}", None

    payload = {
        "group": str(group_name),
        "bot": str(bot_name),
        "pid": os.getpid(),
        "host": socket.gethostname(),
        "acquired_at": int(time.time()),
    }
    try:
        handle.seek(0)
        handle.truncate(0)
        handle.write(json.dumps(payload, ensure_ascii=True))
        handle.flush()
    except OSError:
        pass

    return True, f"Sender-Lock aktiv ({lock_path})", handle
