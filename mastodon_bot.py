import asyncio
import os
import re
import logging
from threading import Lock
from datetime import date, datetime, time, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo
from typing import Any
from PIL import Image
import io
import cairosvg
import json
import time as time_module

import aiohttp
import aiofiles

from mastodon import Mastodon
from google import genai
from google.genai import types  # <- für system_instruction
from gemini_helper import GeminiModelManager
from mastodon_text_utils import split_mastodon_text, sanitize_for_mastodon
import mastodon_post_store
import state_store

GEMINI_KEY_ENV_VARS = [
    "GEMINI_API_KEY",
    "GEMINI_API_KEY1",
    "GEMINI_API_KEY2",
    "GEMINI_API_KEY3",
    "GEMINI_API_KEY4",
]
_gemini_clients: list[tuple[str, Any]] = []
_gemini_client_index = 0
_gemini_client_lock = Lock()


def _build_gemini_clients() -> list[tuple[str, Any]]:
    seen_keys: set[str] = set()
    clients: list[tuple[str, Any]] = []
    for env_name in GEMINI_KEY_ENV_VARS:
        api_key = (os.environ.get(env_name) or "").strip()
        if not api_key or api_key in seen_keys:
            continue
        seen_keys.add(api_key)
        clients.append((env_name, genai.Client(api_key=api_key)))

    if clients:
        return clients

    # Fallback auf das bisherige Verhalten.
    return [("GEMINI_API_KEY", genai.Client(api_key=os.environ.get("GEMINI_API_KEY")))]


def _ensure_gemini_clients():
    global _gemini_clients
    if _gemini_clients:
        return
    _gemini_clients = _build_gemini_clients()


def get_next_gemini_client() -> tuple[Any, str]:
    global _gemini_client_index
    with _gemini_client_lock:
        _ensure_gemini_clients()
        env_name, current_client = _gemini_clients[_gemini_client_index]
        _gemini_client_index = (_gemini_client_index + 1) % len(_gemini_clients)
        return current_client, env_name


def get_primary_gemini_client() -> Any:
    _ensure_gemini_clients()
    return _gemini_clients[0][1]


def get_gemini_client_pool_size() -> int:
    _ensure_gemini_clients()
    return max(1, len(_gemini_clients))


# Initialize the Google Gemini client with the available API keys
client = get_primary_gemini_client()
gemini_manager = GeminiModelManager(client)

# Configure logging
LOG_PATH = '/home/sascha/bots/twitter_bot.log'
ALT_TEXT_LOG_PREFIX = "Alt-Text Generierung"
GEMINI_HELPER_PREFIX = "gemini_helper"
ALT_TEXT_FALLBACK = "Alt-Text konnte nicht automatisch generiert werden."
MASTODON_VERSION_CACHE_MAX_AGE_SECONDS = int(
    os.environ.get("MASTODON_VERSION_CACHE_MAX_AGE_SECONDS", str(7 * 24 * 60 * 60))
)
MASTODON_QUOTE_MIN_VERSION = (4, 5, 0)
QUOTE_POST_UNSUPPORTED_INSTANCES: set[str] = set()
INSTANCE_QUOTE_POLICIES: dict[str, str] = {}
QUOTE_POLICY_DISABLED_VALUES = {"disabled", "deny", "disallow"}

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s:%(message)s',
    force=True,
)

# gemini_helper-Logger: nur Errors in twitter_bot.log, keine zusätzlichen Handler für Info
helper_logger = logging.getLogger("gemini_helper")
if helper_logger.handlers:
    for h in list(helper_logger.handlers):
        h.setLevel(logging.WARNING)
helper_logger.setLevel(logging.WARNING)
helper_logger.propagate = True

# Eigener Alt-Text-Logger mit INFO-Level, ohne globale Log-Flut.
alt_text_logger = logging.getLogger("mastodon_alt_text")
if not alt_text_logger.handlers:
    _alt_handler = logging.FileHandler(LOG_PATH)
    _alt_handler.setLevel(logging.INFO)
    _alt_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s:%(message)s'))
    alt_text_logger.addHandler(_alt_handler)
alt_text_logger.setLevel(logging.INFO)
alt_text_logger.propagate = False
print("Logging configured")

# Pfad für Tagging-Regeln (wird vom Control-Bot gepflegt)
BERLIN_TZ = ZoneInfo("Europe/Berlin")
TAG_DM_MAX = 440


def log_alt_info(msg: str):
    alt_text_logger.info(f"{ALT_TEXT_LOG_PREFIX}: {msg}")


def log_alt_warning(msg: str):
    alt_text_logger.warning(f"{ALT_TEXT_LOG_PREFIX}: {msg}")


def log_alt_error(msg: str):
    alt_text_logger.error(f"{ALT_TEXT_LOG_PREFIX}: {msg}")

# Instances: Tokens kommen direkt aus ENV:
#   opnv_berlin, opnv_toot, opnv_mastodon
instances = {
    'opnv_berlin':   {'access_token_env': 'opnv_berlin',   'api_base_url': 'https://berlin.social'},
    'opnv_toot':     {'access_token_env': 'opnv_toot',     'api_base_url': 'https://toot.berlin'},
    'opnv_mastodon': {'access_token_env': 'opnv_mastodon', 'api_base_url': 'https://mastodon.berlin'}
}

EVENT_ENABLED = os.environ.get("MASTODON_CONTROL_EVENT_ENABLED", "1").lower() not in {"0", "false", "no"}
EVENT_HOST = os.environ.get("MASTODON_CONTROL_EVENT_HOST", "127.0.0.1")
EVENT_PORT = int(os.environ.get("MASTODON_CONTROL_EVENT_PORT", "8123"))

opnv_berlin = ["Servicemeldung", "SBahnBerlin"]
opnv_toot = ["Servicemeldung", "VBB", "bpol", "polizeiberlin", "PolizeiBerlin", "Berliner_Fw", "VIP", "ODEG"]
opnv_mastodon = ["Servicemeldung", "BVG", "VIZ"]


# WICHTIG: Dieser Text wird als SYSTEMPROMPT gesetzt (system_instruction)
alt_text = (
    "Bitte generiere mir für das Bild einen Alternativ-Text für Mastodon – "
    "sehr umfangreich, aber maximal 1500 Zeichen. Antworte ausschließlich mit dem Alt-Text."
)

# Platzhalter für Quota-Sperren, damit generate_alt_text nicht mit NameError abbricht
EXHAUSTED_MODELS: dict[str, datetime] = {}

print("Instances configured")

MASTODON_MAX_CHARS = 500
MASTODON_MIN_CONTENT_LEN = int(os.environ.get("MASTODON_MIN_CONTENT_LEN", "8"))
MASTODON_FIRST_POST_MIN_CONTENT_LEN = int(os.environ.get("MASTODON_FIRST_POST_MIN_CONTENT_LEN", "80"))
SEND_RETRY_DELAYS_SECONDS = [60, 120, 180]
SEND_MAX_EXTRA_RETRIES = len(SEND_RETRY_DELAYS_SECONDS)
INSTANCE_PAUSE_SECONDS = 15 * 60
DEFAULT_NITTER_IMAGE_RETRY_HOSTS = {"localhost", "127.0.0.1", "nitter.nuc.lan"}


def _parse_retry_delays(raw: str | None, fallback: list[int]) -> list[int]:
    tokens = re.split(r"[,\s;]+", (raw or "").strip())
    parsed: list[int] = []
    for token in tokens:
        if not token:
            continue
        try:
            value = int(token)
        except ValueError:
            continue
        if value > 0:
            parsed.append(value)
    return parsed or list(fallback)


def _parse_host_set(raw: str | None, fallback: set[str]) -> set[str]:
    tokens = re.split(r"[,\s;]+", (raw or "").strip().lower())
    hosts = {token for token in tokens if token}
    return hosts or set(fallback)


IMAGE_DOWNLOAD_RETRY_ENABLED = os.environ.get(
    "MASTODON_IMAGE_DOWNLOAD_RETRY_ENABLED", "1"
).lower() not in {"0", "false", "no"}
IMAGE_DOWNLOAD_RETRY_ONLY_NITTER = os.environ.get(
    "MASTODON_IMAGE_DOWNLOAD_RETRY_ONLY_NITTER", "1"
).lower() not in {"0", "false", "no"}
IMAGE_DOWNLOAD_RETRY_DELAYS_SECONDS = _parse_retry_delays(
    os.environ.get("MASTODON_IMAGE_DOWNLOAD_RETRY_DELAYS_SECONDS"),
    SEND_RETRY_DELAYS_SECONDS,
)
IMAGE_DOWNLOAD_RETRY_NITTER_HOSTS = _parse_host_set(
    os.environ.get("MASTODON_IMAGE_DOWNLOAD_RETRY_NITTER_HOSTS"),
    DEFAULT_NITTER_IMAGE_RETRY_HOSTS,
)


def _is_nitter_pic_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = parsed.path or ""
    if not host or "/pic/" not in path:
        return False
    return host in IMAGE_DOWNLOAD_RETRY_NITTER_HOSTS


def _should_retry_image_download(url: str) -> bool:
    if not IMAGE_DOWNLOAD_RETRY_ENABLED or not IMAGE_DOWNLOAD_RETRY_DELAYS_SECONDS:
        return False
    if not IMAGE_DOWNLOAD_RETRY_ONLY_NITTER:
        return True
    return _is_nitter_pic_url(url)


def _is_max_retries_exceeded_error(error_text: str) -> bool:
    msg = (error_text or "").lower()
    return "max retries exceeded with url" in msg


def _pause_instance_if_needed(instance_name: str, error_text: str, *, source: str) -> bool:
    if not _is_max_retries_exceeded_error(error_text):
        return False
    pause_until = state_store.set_mastodon_instance_pause(
        instance_name,
        consumers=["mastodon_bot", "mastodon_control_bot"],
        reporter="mastodon_bot",
        pause_seconds=INSTANCE_PAUSE_SECONDS,
        reason=f"{source}: {error_text}",
    )
    pause_until_dt = datetime.fromtimestamp(pause_until, BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
    logging.warning(
        f"mastodon_bot: Instanz {instance_name} pausiert bis {pause_until_dt} wegen Netzwerkfehler "
        f"(max retries exceeded). Quelle={source}"
    )
    return True


def _extract_status_id_from_url(url: str) -> str:
    if not url:
        return ""
    patterns = [
        r"/status/(\d+)",
        r"/statuses/(\d+)",
        r"/@[^/]+/(\d+)",
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            return m.group(1)
    fallback = re.search(r"/(\d+)(?:\D|$)", url)
    return fallback.group(1) if fallback else ""


def _replace_quote_links_for_instance(
    messages: list[str], quote_refs: list[dict], instance_name: str
) -> list[str]:
    """
    Ersetzt Zitat-Platzhalter durch Mastodon-Links, falls bekannt.
    Belässt den Text ansonsten wie er ist.
    """
    if not messages or not quote_refs:
        return messages

    adjusted = []
    for message in messages:
        updated = message
        for ref in quote_refs:
            display = (ref.get("display") or "").strip()
            status_id = (ref.get("status_id") or "").strip()
            if not display or not status_id:
                continue

            masto_url = mastodon_post_store.get_post(instance_name, status_id)
            if not masto_url:
                continue

            needles = [f"Zitat: {display}"]
            display_sanitized = sanitize_for_mastodon(display)
            if display_sanitized and display_sanitized != display:
                needles.append(f"Zitat: {display_sanitized}")

            for needle in needles:
                if needle in updated:
                    updated = updated.replace(needle, f"Zitat: {masto_url}", 1)
                    break
        adjusted.append(updated)
    return adjusted


def _find_quote_url_for_instance(quote_refs: list[dict], instance_name: str) -> str | None:
    """
    Sucht die Mastodon-URL eines referenzierten Quotes im lokalen Store.
    """
    if not quote_refs:
        return None

    for ref in quote_refs:
        status_id = (ref.get("status_id") or "").strip()
        if not status_id:
            continue
        url = mastodon_post_store.get_post(instance_name, status_id)
        if url:
            return url
    return None


def _parse_version_tuple(version: str) -> tuple[int, int, int]:
    cleaned = (version or "").split("+", 1)[0]
    parts = re.findall(r"\d+", cleaned)
    nums = [int(p) for p in parts[:3]]
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums[:3])


def _is_version_at_least(version: str, minimum: tuple[int, int, int]) -> bool:
    ver = _parse_version_tuple(version)
    min_len = len(minimum)
    ver_len = len(ver)
    padded_ver = list(ver) + [0] * max(0, min_len - ver_len)
    padded_min = list(minimum) + [0] * max(0, ver_len - min_len)
    return tuple(padded_ver[: len(padded_min)]) >= tuple(padded_min[: len(padded_ver)])


def _supports_official_quotes(version: str | None) -> bool:
    return _supports_official_quotes_with_policy(version=version, quote_policy=None)


def _supports_official_quotes_with_policy(version: str | None, quote_policy: str | None) -> bool:
    if not version:
        return False
    if quote_policy:
        policy = str(quote_policy).strip().lower()
        if policy in QUOTE_POLICY_DISABLED_VALUES:
            return False
    return _is_version_at_least(version, MASTODON_QUOTE_MIN_VERSION)


def _is_quote_feature_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "quote_id is only available with feature set fedibird" in msg
        or "quote_id is not available" in msg
        or "quoted_status_id" in msg and ("not" in msg or "unknown" in msg or "available" in msg or "denied" in msg or "policy" in msg or "allowed" in msg)
    )


def _mark_quote_feature_unsupported(instance_name: str, reason: str | None = None):
    if instance_name in QUOTE_POST_UNSUPPORTED_INSTANCES:
        return
    QUOTE_POST_UNSUPPORTED_INSTANCES.add(instance_name)
    msg = f"mastodon_bot: Instanz {instance_name} meldet keinen Quote-API-Support, schalte auf Link-Fallback um."
    if reason:
        msg += f" Grund: {reason}"
    logging.warning(msg)


def _load_instance_versions() -> dict:
    data = state_store.load_mastodon_versions()
    return data if isinstance(data, dict) else {}


def _save_instance_versions(data: dict):
    state_store.save_mastodon_versions(data if isinstance(data, dict) else {})


def _version_cache_entry_is_stale(entry: dict, now_ts: int) -> bool:
    checked_at = entry.get("checked_at")
    if not isinstance(checked_at, (int, float)):
        return True
    return (now_ts - int(checked_at)) > MASTODON_VERSION_CACHE_MAX_AGE_SECONDS


async def _fetch_instance_version(mastodon, instance_name: str | None = None) -> str | None:
    try:
        info = await asyncio.to_thread(mastodon.instance)
    except Exception as e:
        if instance_name:
            _pause_instance_if_needed(instance_name, str(e), source="fetch_instance_version")
        logging.error(f"mastodon_bot: Fehler beim Abrufen der Instanzversion: {e}")
        return None

    if instance_name:
        try:
            quote_policy = None
            if hasattr(info, "get"):
                quote_policy = (info.get("quote_approval_policy") or "").strip() or None
                cfg = info.get("configuration") if isinstance(info.get("configuration"), dict) else {}
                if not quote_policy and cfg:
                    quote_policy = (cfg.get("quote_approval_policy") or "").strip() or None
                statuses_cfg = cfg.get("statuses") if isinstance(cfg.get("statuses"), dict) else {}
                if not quote_policy and statuses_cfg:
                    quote_policy = (statuses_cfg.get("quote_approval_policy") or "").strip() or None
            else:
                quote_policy = getattr(info, "quote_approval_policy", None)
                if quote_policy:
                    quote_policy = str(quote_policy).strip()

            if quote_policy:
                INSTANCE_QUOTE_POLICIES[instance_name] = quote_policy
        except Exception:
            pass

    try:
        version = info.get("version") if hasattr(info, "get") else getattr(info, "version", None)
        if isinstance(version, str) and version.strip():
            return version.strip()
    except Exception:
        pass

    logging.warning("mastodon_bot: Instanzversion konnte nicht gelesen werden.")
    return None


async def _ensure_instance_version(
    instance_name: str,
    mastodon,
    min_required: tuple[int, int, int] = MASTODON_QUOTE_MIN_VERSION,
) -> tuple[str | None, bool]:
    """
    Liefert die Instanzversion. Fragt nur dann beim Server nach, wenn keine
    Version bekannt ist oder die bekannte Version das Mindestkriterium nicht erfüllt.
    Rückgabe: (version, cache_updated)
    """
    cache = _load_instance_versions()
    now_ts = int(time_module.time())
    entry = cache.get(instance_name) if isinstance(cache, dict) else None
    cache_needs_save = False

    if entry:
        version = entry.get("version") if isinstance(entry, dict) else None
        version_str = version if isinstance(version, str) else None
        if version_str and _is_version_at_least(version_str, min_required):
            # Bereits ausreichend, kein Re-Check nötig.
            return version_str, False
        if not _version_cache_entry_is_stale(entry, now_ts):
            return version_str, False

    version = await _fetch_instance_version(mastodon, instance_name=instance_name)
    if version:
        cache[instance_name] = {"version": version, "checked_at": now_ts}
        _save_instance_versions(cache)
        cache_needs_save = True
        return version, cache_needs_save

    # Kein neuer Wert: ggf. alten Wert nutzen
    if entry and isinstance(entry.get("version"), str):
        # Aktualisiere den Check-Zeitpunkt, damit nicht bei jedem Lauf neu abgefragt wird.
        entry["checked_at"] = now_ts
        cache[instance_name] = entry
        _save_instance_versions(cache)
        return entry["version"], True

    return None, cache_needs_save


def _resolve_quote_id_for_instance(quote_refs: list[dict], instance_name: str) -> str | None:
    """
    Holt das Mastodon-Status-ID für ein Quote, falls im lokalen Store vorhanden.
    """
    url = _find_quote_url_for_instance(quote_refs, instance_name)
    if not url:
        return None
    masto_status_id = _extract_status_id_from_url(url)
    return masto_status_id if masto_status_id else None


def _empty_rules() -> dict:
    return {"users": {}}


def load_tagging_rules() -> dict:
    data = state_store.load_mastodon_rules()
    if not isinstance(data, dict):
        return _empty_rules()
    data.setdefault("users", {})
    return data


async def notify_control_bot(instance_name: str, status_obj: dict, content_raw: str):
    if not EVENT_ENABLED:
        return

    try:
        status_id = status_obj.get("id") if hasattr(status_obj, "get") else getattr(status_obj, "id", None)
        status_url = status_obj.get("url") if hasattr(status_obj, "get") else getattr(status_obj, "url", "")
    except Exception:
        status_id = None
        status_url = ""

    payload = {
        "instance": instance_name,
        "status_id": status_id,
        "url": status_url,
        "content": content_raw,
    }

    try:
        reader, writer = await asyncio.wait_for(asyncio.open_connection(EVENT_HOST, EVENT_PORT), timeout=2.0)
    except Exception:
        # Event-Brücke nicht erreichbar -> stumm überspringen
        return

    try:
        data = json.dumps(payload).encode("utf-8")
        writer.write(data)
        await writer.drain()
    except Exception:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


def normalize_acct(acct: str) -> str:
    return (acct or "").lstrip("@").strip()


def _easter_date(year: int) -> date:
    # Anonymous Gregorian algorithm
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def is_berlin_holiday(d: date) -> bool:
    try:
        easter = _easter_date(d.year)
    except Exception:
        return False

    holidays = {
        date(d.year, 1, 1),    # Neujahr
        date(d.year, 3, 8),    # Intl. Frauentag (Berlin)
        date(d.year, 5, 1),    # Tag der Arbeit
        date(d.year, 10, 3),   # Tag der Deutschen Einheit
        date(d.year, 12, 25),  # 1. Weihnachtstag
        date(d.year, 12, 26),  # 2. Weihnachtstag
    }

    good_friday = easter - timedelta(days=2)
    easter_monday = easter + timedelta(days=1)
    ascension = easter + timedelta(days=39)
    whit_monday = easter + timedelta(days=50)

    holidays.update({good_friday, easter_monday, ascension, whit_monday})
    return d in holidays


def _time_in_window(now_t: time, start: str, end: str) -> bool:
    try:
        s_h, s_m = map(int, start.split(":"))
        e_h, e_m = map(int, end.split(":"))
        s = time(s_h, s_m)
        e = time(e_h, e_m)
    except Exception:
        return True

    if s <= e:
        return s <= now_t <= e
    return now_t >= s or now_t <= e


def schedule_allows(schedule: dict | None, now: datetime) -> bool:
    if not schedule:
        return True

    windows = schedule.get("windows") or []
    days_mode = (schedule.get("days") or "all").lower()
    skip_holidays = bool(schedule.get("skip_holidays"))

    d = now.date()
    if days_mode == "mon-fri":
        if d.weekday() > 4:
            return False
    elif days_mode == "mon-sat":
        if d.weekday() > 5:
            return False

    if skip_holidays and is_berlin_holiday(d):
        return False

    if not windows:
        return True

    now_t = now.time()
    for w in windows:
        start = w.get("start")
        end = w.get("end")
        if start and end and _time_in_window(now_t, start, end):
            return True
    return False


def _date_from_field(val) -> date | None:
    if isinstance(val, date):
        return val
    if isinstance(val, datetime):
        return val.date()
    if isinstance(val, str) and val.strip():
        try:
            return datetime.fromisoformat(val.strip()).date()
        except Exception:
            return None
    return None


def validity_allows(rule: dict, today: date) -> bool:
    start = _date_from_field(rule.get("valid_from"))
    end = _date_from_field(rule.get("valid_until"))
    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


def _match_keywords(content: str, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    lower = content.lower()
    hits = []
    for kw in keywords:
        if kw and kw.lower() in lower:
            hits.append(kw)
    return hits


def _shorten(text: str, max_len: int = 240) -> str:
    t = (text or "").strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


async def send_direct_reply(mastodon, status: dict, acct: str, text: str):
    prefix = f"@{normalize_acct(acct)} "
    max_len = max(1, TAG_DM_MAX - len(prefix))
    body_parts = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_len:
            body_parts.append(remaining)
            break
        chunk = remaining[:max_len]
        split_at = max_len
        separators = ["\n\n", "\n", ". ", ", ", " "]
        for sep in separators:
            idx = chunk.rfind(sep)
            if idx > 0:
                split_at = idx + len(sep)
                break
        body_parts.append(remaining[:split_at].rstrip())
        remaining = remaining[split_at:].lstrip()

    for part in body_parts:
        try:
            await asyncio.to_thread(
                mastodon.status_post,
                f"{prefix}{part}",
                visibility="direct",
                in_reply_to_id=status.get("id")
            )
        except Exception as e:
            logging.error(f"mastodon_bot: Fehler beim direkten Reply: {e}")


async def tag_users_for_status(mastodon, status: dict, content: str, rules_data: dict, instance_name: str):
    users = rules_data.get("users") or {}
    if not users:
        return

    now = datetime.now(BERLIN_TZ)
    today = now.date()
    status_url = status.get("url") or status.get("uri") or ""
    for acct, cfg in users.items():
        if cfg.get("global_pause"):
            continue

        triggered: set[str] = set()
        schedule_cache: dict[str, Any] = {}
        for rule in cfg.get("rules", []):
            if rule.get("paused"):
                continue
            if not validity_allows(rule, today):
                continue
            insts = rule.get("instances") or ["alle"]
            if "alle" not in insts and instance_name not in insts:
                continue
            sched = rule.get("schedule") or cfg.get("global_schedule")
            sched_key = json.dumps(sched, sort_keys=True) if sched else "none"
            if sched_key not in schedule_cache:
                schedule_cache[sched_key] = schedule_allows(sched, now)
            if not schedule_cache[sched_key]:
                continue
            hits = _match_keywords(content, rule.get("keywords") or [])
            blocked_hits = _match_keywords(content, rule.get("blocked_keywords") or [])
            if blocked_hits:
                continue
            triggered.update(hits)

        if not triggered:
            continue

        preview = _shorten(content, 220)
        kw_lines = "\n".join([f"- {k}" for k in sorted(triggered)])
        msg = (
            f"Dein Filter hat ausgelöst ({instance_name}).\n"
            f"Stichworte:\n{kw_lines}\n"
            f"{status_url}\n\n"
            f"{preview}"
        ).strip()
        await send_direct_reply(mastodon, status, acct, msg)


def _extract_core_content(message: str, username: str) -> str:
    """
    Entfernt Header/Metadaten, um die eigentliche Textlänge zu bestimmen.
    """
    body = (message or "").strip()
    header_prefix = f"#{username}:"
    if body.startswith(header_prefix):
        body = body[len(header_prefix) :].lstrip()

    body = body.replace("#öpnv_berlin_bot", "")
    filtered_lines = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("src:"):
            continue
        if re.match(r"^\d{1,2}\.\d{1,2}\.\d{4}", stripped):
            continue
        filtered_lines.append(stripped)

    return "\n".join(filtered_lines).strip()


def filter_short_mastodon_messages(
    messages: list[str],
    username: str,
    min_len: int,
    keep_all_parts: bool = False,
) -> list[str]:
    """
    Entfernt Posts, deren Kerntext kürzer als die Mindestlänge ist.
    Bei mehrteiligen Posts können per keep_all_parts alle Segmente behalten werden,
    damit Header/Meta-Blöcke beim Thread-Split nicht verloren gehen.
    """
    if min_len <= 0:
        return messages

    if keep_all_parts and len(messages) > 1:
        return messages

    filtered: list[str] = []
    for msg in messages:
        core = _extract_core_content(msg, username)
        if len(core) < min_len:
            logging.warning(
                f"mastodon_bot: Überspringe Mastodon-Post (zu kurz: {len(core)} Zeichen) für {username}"
            )
            continue
        filtered.append(msg)

    return filtered


def build_mastodon_messages(
    username: str,
    tweet_text: str,
    var_href: str | None,
    extern_urls: str | None,
    posted_time: str | None,
    max_len: int = MASTODON_MAX_CHARS,
    min_len: int = MASTODON_MIN_CONTENT_LEN,
) -> list[str]:
    """
    Assemble a Mastodon status and split it into thread parts when it exceeds the limit.
    Header stays on the first post, metadata goes to the end.
    """
    tweet_text = tweet_text or ""
    header = f"#{username}:\n\n"
    footer = "\n\n#öpnv_berlin_bot"

    src_line = f"\n\nsrc: {var_href}" if var_href else ""
    urls_line = f"\n{extern_urls}" if extern_urls else ""
    time_line = f"\n{posted_time}" if posted_time else ""

    full_message = f"{header}{tweet_text}"
    if urls_line:
        full_message += urls_line
    full_message += footer
    if src_line:
        full_message += src_line
    if time_line:
        full_message += time_line

    first_part_min_len = 0
    if len(full_message) > max_len:
        desired_core = max(min_len, MASTODON_FIRST_POST_MIN_CONTENT_LEN)
        first_part_min_len = min(max_len, len(header) + desired_core)

    return split_mastodon_text(
        full_message,
        max_len=max_len,
        min_len=min_len,
        first_min_len=first_part_min_len,
    )

def _status_post_with_quote_support(
    *,
    mastodon,
    message: str,
    visibility: str,
    in_reply_to_id=None,
    media_ids: list | None = None,
    quoted_status_id: str | None = None,
):
    """
    Send a status using the official quoted_status_id parameter when provided.
    Falls back to the underlying Mastodon client for ID unpacking and request handling.
    """
    try:
        unpack_id = mastodon._Mastodon__unpack_id  # type: ignore[attr-defined]
    except Exception:
        unpack_id = lambda x: x  # noqa: E731

    params: dict[str, Any] = {
        "status": message,
        "visibility": (visibility or "").lower() if visibility else None,
    }
    if in_reply_to_id is not None:
        try:
            params["in_reply_to_id"] = unpack_id(in_reply_to_id)
        except Exception:
            params["in_reply_to_id"] = in_reply_to_id
    if media_ids:
        try:
            params["media_ids"] = [unpack_id(mid) for mid in media_ids]
        except Exception:
            params["media_ids"] = media_ids
    if quoted_status_id:
        try:
            params["quoted_status_id"] = unpack_id(quoted_status_id)
        except Exception:
            params["quoted_status_id"] = quoted_status_id

    # Clean out None values to match Mastodon API expectations
    params = {k: v for k, v in params.items() if v is not None}
    return mastodon._Mastodon__api_request(  # type: ignore[attr-defined]
        "POST",
        "/api/v1/statuses",
        params,
    )


async def post_tweet(
    mastodon,
    message,
    username,
    instance_name,
    in_reply_to_id=None,
    quoted_status_id: str | None = None,
):
    try:
        if instance_name == "opnv_berlin":
            visibility = 'public' if any(sub in username for sub in opnv_berlin) else 'unlisted'
        elif instance_name == "opnv_toot":
            visibility = 'public' if any(sub in username for sub in opnv_toot) else 'unlisted'
        elif instance_name == "opnv_mastodon":
            visibility = 'public' if any(sub in username for sub in opnv_mastodon) else 'unlisted'
        else:
            logging.error("Instanz nicht gefunden")
            return None
        if quoted_status_id:
            return await asyncio.to_thread(
                _status_post_with_quote_support,
                mastodon=mastodon,
                message=message,
                visibility=visibility,
                in_reply_to_id=in_reply_to_id,
                media_ids=None,
                quoted_status_id=quoted_status_id,
            )

        return await asyncio.to_thread(
            mastodon.status_post,
            message,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id
        )
    except Exception as e:
        if _is_quote_feature_error(e):
            _mark_quote_feature_unsupported(instance_name, str(e))
            logging.warning(
                f"mastodon_bot: Quote-API fehlt auf {instance_name}, poste ohne offizielle Quote (text-only)."
            )
            return None
        _pause_instance_if_needed(instance_name, str(e), source="post_tweet")
        logging.error(f"mastodon_bot: Fehler beim Posten auf {instance_name}: {e}")
        return None


def extract_hashtags(content, username):
    if username.startswith("@"):
        username = username[1:]
    hashtags = ""
    for word in content.split():
        if word.startswith("#") and len(word) > 1:
            word = word.replace('.', '').replace(',', '').replace(':', '').replace(';', '')
            hashtags += f" {word}_{username}"
    return hashtags


async def download_image(session, url):
    retry_delays = IMAGE_DOWNLOAD_RETRY_DELAYS_SECONDS if _should_retry_image_download(url) else []
    total_attempts = 1 + len(retry_delays)

    for attempt in range(1, total_attempts + 1):
        error_detail = ""
        try:
            async with session.get(url) as response:
                if response.status == 200:
                    if attempt > 1:
                        logging.warning(
                            "mastodon_bot: Bild-Download nach Retry erfolgreich "
                            f"(versuch={attempt}/{total_attempts}): {url}"
                        )
                    return await response.read()
                error_detail = f"HTTP {response.status}"
        except Exception as e:
            error_detail = str(e)

        if attempt <= len(retry_delays):
            delay = retry_delays[attempt - 1]
            logging.warning(
                "mastodon_bot: Bild-Download fehlgeschlagen "
                f"(versuch={attempt}/{total_attempts}, reason={error_detail}), "
                f"retry in {delay}s: {url}"
            )
            await asyncio.sleep(delay)
            continue

        logging.error(
            "mastodon_bot: Fehler beim Herunterladen des Bildes "
            f"(versuch={attempt}/{total_attempts}, reason={error_detail}): {url}"
        )
        return None

    return None


async def download_binary(session, url):
    try:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.read(), response.headers.get("Content-Type", "")
            logging.error(f"mastodon_bot: Fehler beim Herunterladen der Datei: {url}")
            return None, ""
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler beim Herunterladen der Datei: {e}")
        return None, ""


async def generate_alt_text(
    client,
    image_bytes: bytes,
    original_tweet_full: str,
    twitter_account: str | None = None,
    tweet_url: str | None = None
):
    """
    SYSTEMPROMPT: alt_text (system_instruction)
    USER-CONTENT: Kontext inkl. Quelle (Twitter Account) + optional Tweet-URL + Original-Tweet (vollständig)
    """
    now = datetime.now()
    # Abgelaufene Quota-Sperren säubern
    for model_name, until in list(EXHAUSTED_MODELS.items()):
        if until <= now:
            EXHAUSTED_MODELS.pop(model_name, None)

    twitter_account = (twitter_account or "").lstrip("@").strip()
    source_line = f"Quelle (Twitter/X Account): @{twitter_account}" if twitter_account else "Quelle (Twitter/X Account): unbekannt"
    url_line = f"Tweet-URL: {tweet_url}" if tweet_url else ""

    context_text = (
        f"{source_line}\n"
        f"{url_line}\n"
        "Original-Tweet (vollständig, unverändert):\n"
        f"{original_tweet_full}"
    ).strip()

    cfg = types.GenerateContentConfig(
        system_instruction=alt_text
    )

    def _is_retryable_api_error(msg: str) -> bool:
        m = msg.lower()
        return (
            "resource_exhausted" in m
            or "quota" in m
            or "exceeded your current quota" in m
            or "429" in m
            or "503" in m
            or "unavailable" in m
        )

    def _is_definitive_api_error(msg: str) -> bool:
        m = msg.lower()
        return (
            "googleapierror" in m
            or "grpc" in m
            or "status_code" in m
            or "http" in m
            or "invalid" in m
            or "permission" in m
            or "unauthorized" in m
            or "forbidden" in m
            or "not found" in m
            or "400" in m
            or "401" in m
            or "403" in m
            or "404" in m
        )

    if client is None:
        discovery_client, _ = get_next_gemini_client()
        gemini_manager.client = discovery_client

    failed_models: list[str] = []

    for model_name in gemini_manager.get_candidate_models():
        pool_attempts = get_gemini_client_pool_size()
        use_custom_client = client is not None
        max_attempts = pool_attempts + (1 if use_custom_client else 0)
        retryable_errors: list[str] = []
        not_found_errors: list[str] = []
        non_retryable_errors: list[str] = []

        for attempt in range(max_attempts):
            if use_custom_client and attempt == 0:
                active_client = client
                key_source = "custom_client"
            else:
                active_client, key_source = get_next_gemini_client()
            gemini_manager.client = active_client

            try:
                with Image.open(io.BytesIO(image_bytes)) as img:
                    response = active_client.models.generate_content(
                        model=model_name,
                        config=cfg,
                        contents=[
                            context_text,  # Kontext als User-Content
                            img
                        ]
                    )
                text = (response.text or "").strip()
                if not text:
                    msg = "Leere Antwort vom Modell"
                    non_retryable_errors.append(msg)
                    log_alt_info(f"Modell '{model_name}' über {key_source}: {msg}")
                    if attempt + 1 < max_attempts:
                        log_alt_info(f"Modell '{model_name}': leere Antwort, nächster Key wird versucht.")
                        continue
                    break

                gemini_manager.mark_success(model_name)
                return text[:1500]
            except Exception as e:
                err_txt = str(e)
                if "not found" in err_txt.lower() or "404" in err_txt:
                    not_found_errors.append(err_txt)
                    if attempt + 1 < max_attempts:
                        log_alt_info(f"Modell '{model_name}' über {key_source}: 404/NOT_FOUND, nächster Key wird versucht.")
                        continue
                    break

                if _is_retryable_api_error(err_txt):
                    retryable_errors.append(err_txt)
                    log_alt_info(f"Modell '{model_name}' über {key_source} schlug fehl: {err_txt}")
                    if attempt + 1 < max_attempts:
                        log_alt_info(
                            f"Modell '{model_name}': Retry mit nächstem Gemini-Key ({attempt + 1}/{max_attempts - 1} Wechsel)."
                        )
                        continue
                    break

                non_retryable_errors.append(err_txt)
                if attempt + 1 < max_attempts:
                    log_alt_info(f"Modell '{model_name}' über {key_source}: unerwarteter Fehler, nächster Key wird versucht.")
                    continue
                break

        if retryable_errors and len(retryable_errors) >= max_attempts:
            reason = retryable_errors[-1]
            log_alt_info(
                f"Modell '{model_name}' scheiterte nach Rotation über alle verfügbaren Gemini-Keys: {retryable_errors[-1]}"
            )
            gemini_manager.mark_quota(model_name, reason)
            failed_models.append(f"{model_name} (quota/unavailable): {reason}")
            continue

        if not_found_errors and len(not_found_errors) >= max_attempts:
            reason = not_found_errors[-1]
            log_alt_warning(f"Modell '{model_name}' meldet API-Fehler NOT_FOUND: {reason}")
            gemini_manager.mark_not_found(model_name, reason)
            failed_models.append(f"{model_name} (not_found): {reason}")
            continue

        if retryable_errors or not_found_errors:
            reason = (retryable_errors + not_found_errors)[-1]
            if _is_definitive_api_error(reason):
                log_alt_warning(f"Modell '{model_name}' meldet gemischte API-Fehler über mehrere Keys: {reason}")
            else:
                log_alt_info(f"Modell '{model_name}' meldet gemischte API-Fehler über mehrere Keys: {reason}")
            gemini_manager.mark_failed(model_name, reason)
            failed_models.append(f"{model_name} (mixed_api_errors): {reason}")
            continue

        if non_retryable_errors:
            reason = non_retryable_errors[-1]
            if _is_definitive_api_error(reason):
                log_alt_warning(f"Modell '{model_name}' meldet API-Fehler: {reason}")
            else:
                log_alt_info(f"Modell '{model_name}' scheiterte mit nicht-API-Fehler: {reason}")
            gemini_manager.mark_failed(model_name, reason)
            failed_models.append(f"{model_name} (failed): {reason}")
            continue

    if failed_models:
        log_alt_error(
            f"Konnte kein Alt-Text generieren – alle Modelle schlugen fehl. Letzter Fehler: {failed_models[-1]}"
        )
    else:
        log_alt_error("Konnte kein Alt-Text generieren – alle Modelle schlugen fehl.")
    return ""


def process_image_for_mastodon(image_bytes):
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            target_format = img.format if img.format in ["JPEG", "PNG"] else "JPEG"
            if img.width > 1280 or img.height > 1280:
                img.thumbnail((1280, 1280))
            quality = 85
            while True:
                output_io = io.BytesIO()
                if target_format == "JPEG":
                    img.save(output_io, format=target_format, quality=quality)
                else:
                    img.save(output_io, format=target_format)
                processed_bytes = output_io.getvalue()
                if len(processed_bytes) <= 8 * 1024 * 1024 or quality < 20:
                    return processed_bytes
                quality -= 10
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler beim Verarbeiten des Bildes: {e}")
        return None


def prepare_image_for_upload(orig_image_bytes, ext):
    try:
        if ext == '.svg':
            orig_image_bytes = cairosvg.svg2png(bytestring=orig_image_bytes)
        elif ext not in ['.jpg', '.jpeg', '.png']:
            with Image.open(io.BytesIO(orig_image_bytes)) as img:
                output_io = io.BytesIO()
                img.convert('RGB').save(output_io, format='JPEG')
                orig_image_bytes = output_io.getvalue()
        return process_image_for_mastodon(orig_image_bytes)
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler bei Bildkonvertierung ({ext}): {e}")
        return None


async def prepare_media_payloads(images, original_tweet_full: str, twitter_account: str, tweet_url: str):
    """
    Holt Bilder (lokal oder remote), konvertiert sie und generiert je Bild einmal Alt-Text.
    Gibt Liste von Payloads mit Bytes + Alt-Text zurück.
    """
    payloads = []
    images = images[:4]  # Mastodon: max 4 Bilder
    async with aiohttp.ClientSession() as session:
        for image_link in images:
            try:
                img_bytes = None
                ext = os.path.splitext(image_link)[1].lower() if isinstance(image_link, str) else ".jpg"

                if isinstance(image_link, str) and os.path.isfile(image_link):
                    try:
                        async with aiofiles.open(image_link, 'rb') as image_file:
                            img_bytes = await image_file.read()
                    except Exception as e:
                        logging.error(f"mastodon_bot: Fehler beim Lesen des Bildes {image_link}: {e}")
                        continue
                else:
                    img_bytes = await download_image(session, str(image_link))

                if not img_bytes:
                    logging.error("mastodon_bot: Kein Bild erhalten, überspringe dieses Bild.")
                    continue

                processed_bytes = prepare_image_for_upload(img_bytes, ext)
                if not processed_bytes:
                    logging.error("mastodon_bot: Bildverarbeitung fehlgeschlagen, überspringe dieses Bild.")
                    continue

                try:
                    image_description = await generate_alt_text(
                        client=None,
                        image_bytes=processed_bytes,
                        original_tweet_full=original_tweet_full,
                        twitter_account=twitter_account,
                        tweet_url=tweet_url
                    )
                except Exception as e:
                    log_alt_error(f"Fehler bei Alt-Text-Generierung: {e}")
                    helper_logger.warning(f"{GEMINI_HELPER_PREFIX}: Fehler bei Alt-Text-Generierung: {e}")
                    image_description = ""

                if not image_description:
                    image_description = ALT_TEXT_FALLBACK

                payloads.append({
                    "bytes": processed_bytes,
                    "alt_text": image_description or "",
                    "mime_type": "image/jpeg"
                })
            except Exception as e:
                logging.error(f"mastodon_bot: Unerwarteter Fehler bei Bild-Pipeline ({image_link}): {e}")
                continue

    return payloads


async def prepare_video_payload(video_url: str, tweet_text: str, twitter_account: str, tweet_url: str):
    """
    Lädt ein Video und erstellt einen Alt-Text auf Basis des Tweet-Texts.
    Falls Download fehlschlägt, return None.
    """
    try:
        async with aiohttp.ClientSession() as session:
            video_bytes, content_type = await download_binary(session, str(video_url))
            if not video_bytes:
                return None

        mime = content_type or "video/mp4"
        if "video" not in mime:
            mime = "video/mp4"

        base = tweet_text.strip() or "Video ohne erkennbaren Textinhalt."
        source_line = f"Quelle: @{twitter_account}" if twitter_account else "Quelle: unbekannt"
        url_line = f"URL: {tweet_url}" if tweet_url else ""
        alt_text_video = _shorten(f"Video aus Tweet. {source_line}. {url_line}. Inhalt: {base}", 1500)

        return {
            "bytes": video_bytes,
            "alt_text": alt_text_video,
            "mime_type": mime,
        }
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler bei Video-Verarbeitung ({video_url}): {e}")
        return None


async def upload_media_payloads(mastodon, media_payloads, instance_name: str | None = None):
    media_ids = []
    if not media_payloads:
        return media_ids

    for payload in media_payloads:
        try:
            mime = payload.get("mime_type") or "image/jpeg"
            desc = payload.get("alt_text") or ""
            media_info = await asyncio.to_thread(
                mastodon.media_post,
                io.BytesIO(payload.get("bytes") or b""),
                description=desc,
                mime_type=mime
            )
            media_ids.append(media_info['id'])
        except Exception as e:
            if instance_name:
                _pause_instance_if_needed(instance_name, str(e), source="upload_media_payloads.media_post")
            logging.error(f"mastodon_bot: Fehler beim Media-Post: {e}")
    if not media_ids:
        logging.error("mastodon_bot: Keine Medien konnten hochgeladen werden (0/{}).".format(len(media_payloads)))
    elif len(media_ids) < len(media_payloads):
        logging.warning(
            "mastodon_bot: Nur ein Teil der Medien wurde hochgeladen (%s/%s).",
            len(media_ids),
            len(media_payloads),
        )
    return media_ids


async def post_tweet_with_media(
    mastodon,
    message,
    media_payloads,
    instance_name,
    username: str,
    in_reply_to_id=None,
    quoted_status_id: str | None = None,
):
    try:
        media_ids = await upload_media_payloads(
            mastodon=mastodon,
            media_payloads=media_payloads,
            instance_name=instance_name,
        )
    except Exception as e:
        _pause_instance_if_needed(instance_name, str(e), source="post_tweet_with_media.upload_media_payloads")
        logging.error(f"mastodon_bot: Fehler beim Hochladen der Medien: {e}")
        return
    if media_payloads and not media_ids:
        logging.error("mastodon_bot: Medien-Upload fehlgeschlagen, breche Posting ab.")
        return None

    try:
        if instance_name == "opnv_berlin":
            visibility = 'public' if any(sub in username for sub in opnv_berlin) else 'unlisted'
        elif instance_name == "opnv_toot":
            visibility = 'public' if any(sub in username for sub in opnv_toot) else 'unlisted'
        elif instance_name == "opnv_mastodon":
            visibility = 'public' if any(sub in username for sub in opnv_mastodon) else 'unlisted'
        else:
            logging.error("mastodon_bot: Instanz nicht gefunden")
            return

        if quoted_status_id:
            return await asyncio.to_thread(
                _status_post_with_quote_support,
                mastodon=mastodon,
                message=message,
                visibility=visibility,
                in_reply_to_id=in_reply_to_id,
                media_ids=media_ids,
                quoted_status_id=quoted_status_id,
            )

        return await asyncio.to_thread(
            mastodon.status_post,
            message,
            media_ids=media_ids,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id
        )

    except Exception as e:
        if _is_quote_feature_error(e):
            _mark_quote_feature_unsupported(instance_name, str(e))
            logging.warning(
                f"mastodon_bot: Quote-API fehlt auf {instance_name}, poste ohne offizielle Quote (media)."
            )
            return None
        _pause_instance_if_needed(instance_name, str(e), source="post_tweet_with_media.status_post")
        logging.error(f"mastodon_bot: Allgemeiner Fehler beim Posten mit Bildern: {e}")
        return None


async def post_tweet_with_images_legacy(
    mastodon,
    message,
    media_payloads,
    instance_name,
    username: str,
    in_reply_to_id=None,
    quoted_status_id: str | None = None,
):
    """
    Legacy-Bilder-Posting (Fallback), falls der generische Medien-Upload scheitert.
    """
    try:
        # Falls payloads doch Videos enthalten, hier abbrechen
        for payload in media_payloads:
            if payload.get("mime_type", "").startswith("video/"):
                return None
    except Exception:
        pass

    try:
        media_ids = await upload_media_payloads(
            mastodon=mastodon,
            media_payloads=media_payloads,
            instance_name=instance_name,
        )
    except Exception as e:
        _pause_instance_if_needed(instance_name, str(e), source="post_tweet_with_images_legacy.upload_media_payloads")
        logging.error(f"mastodon_bot: Fallback-Fehler beim Hochladen der Bilder: {e}")
        return
    if media_payloads and not media_ids:
        logging.error("mastodon_bot: Fallback-Medien-Upload fehlgeschlagen, breche Posting ab.")
        return None

    try:
        if instance_name == "opnv_berlin":
            visibility = 'public' if any(sub in username for sub in opnv_berlin) else 'unlisted'
        elif instance_name == "opnv_toot":
            visibility = 'public' if any(sub in username for sub in opnv_toot) else 'unlisted'
        elif instance_name == "opnv_mastodon":
            visibility = 'public' if any(sub in username for sub in opnv_mastodon) else 'unlisted'
        else:
            logging.error("mastodon_bot: Instanz nicht gefunden")
            return

        if quoted_status_id:
            return await asyncio.to_thread(
                _status_post_with_quote_support,
                mastodon=mastodon,
                message=message,
                visibility=visibility,
                in_reply_to_id=in_reply_to_id,
                media_ids=media_ids,
                quoted_status_id=quoted_status_id,
            )

        return await asyncio.to_thread(
            mastodon.status_post,
            message,
            media_ids=media_ids,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id
        )
    except Exception as e:
        if _is_quote_feature_error(e):
            _mark_quote_feature_unsupported(instance_name, str(e))
            logging.warning(
                f"mastodon_bot: Quote-API fehlt auf {instance_name}, poste ohne offizielle Quote (legacy media)."
            )
            return None
        _pause_instance_if_needed(instance_name, str(e), source="post_tweet_with_images_legacy.status_post")
        logging.error(f"mastodon_bot: Fallback-Fehler beim Posten mit Bildern: {e}")
        return None


async def _post_with_media_fallbacks(
    *,
    mastodon,
    message: str,
    attach_media: list,
    instance_name: str,
    username: str,
    in_reply_to_id=None,
    quoted_status_id: str | None = None,
    use_video: bool = False,
    images: list | None = None,
    image_payloads=None,
    original_tweet_full: str = "",
    tweet_url: str = "",
):
    status_obj = None
    if attach_media:
        status_obj = await post_tweet_with_media(
            mastodon=mastodon,
            message=message,
            media_payloads=attach_media,
            instance_name=instance_name,
            username=username,
            in_reply_to_id=in_reply_to_id,
            quoted_status_id=quoted_status_id,
        )
        if status_obj is None and use_video and images:
            if image_payloads is None:
                image_payloads = await prepare_media_payloads(
                    images=images,
                    original_tweet_full=original_tweet_full,
                    twitter_account=username,
                    tweet_url=tweet_url,
                )
            if image_payloads:
                status_obj = await post_tweet_with_media(
                    mastodon=mastodon,
                    message=message,
                    media_payloads=image_payloads,
                    instance_name=instance_name,
                    username=username,
                    in_reply_to_id=in_reply_to_id,
                    quoted_status_id=quoted_status_id,
                )
        if status_obj is None and not use_video and image_payloads:
            status_obj = await post_tweet_with_images_legacy(
                mastodon=mastodon,
                message=message,
                media_payloads=image_payloads,
                instance_name=instance_name,
                username=username,
                in_reply_to_id=in_reply_to_id,
                quoted_status_id=quoted_status_id,
            )
    else:
        status_obj = await post_tweet(
            mastodon,
            message,
            username,
            instance_name,
            in_reply_to_id=in_reply_to_id,
            quoted_status_id=quoted_status_id,
        )

    return status_obj, image_payloads


def _build_mastodon_retry_payload(
    *,
    instance_name: str,
    username: str,
    message: str,
    in_reply_to_id: str | None,
    quoted_status_id: str | None,
    with_media: bool,
    use_video: bool,
    images: list,
    videos: list,
    original_tweet_full: str,
    tweet_url: str,
    idx: int,
    status_id_raw: str,
) -> dict:
    return {
        "instance_name": instance_name,
        "username": username,
        "message": message,
        "in_reply_to_id": in_reply_to_id,
        "quoted_status_id": quoted_status_id,
        "with_media": bool(with_media),
        "use_video": bool(use_video),
        "images": list(images or []),
        "videos": list(videos or []),
        "original_tweet_full": original_tweet_full or "",
        "tweet_url": tweet_url or "",
        "idx": int(idx),
        "status_id_raw": status_id_raw or "",
    }


def _enqueue_mastodon_retry(payload: dict, error_text: str):
    instance_name = str(payload.get("instance_name") or "")
    state_store.enqueue_failed_delivery(
        channel="mastodon",
        target=instance_name,
        payload=payload,
        max_retries=SEND_MAX_EXTRA_RETRIES,
        first_delay_seconds=SEND_RETRY_DELAYS_SECONDS[0],
        last_error=error_text,
    )
    logging.warning(
        f"mastodon_bot: Retry eingeplant für Instanz {instance_name} "
        f"(delays={SEND_RETRY_DELAYS_SECONDS}, reason={error_text})"
    )


async def _process_pending_mastodon_retries(clients: list[tuple[str, Any]]):
    pending = state_store.get_due_failed_deliveries("mastodon", limit=200)
    if not pending:
        return

    client_map = {instance_name: mastodon for instance_name, mastodon in clients}

    for entry in pending:
        delivery_id = int(entry.get("id", 0))
        payload = entry.get("payload", {}) if isinstance(entry.get("payload"), dict) else {}
        instance_name = str(payload.get("instance_name") or entry.get("target") or "")
        mastodon = client_map.get(instance_name)
        now_ts = int(time_module.time())
        pause_until = state_store.get_mastodon_instance_pause_until(
            instance_name,
            consumer="mastodon_bot",
            now_ts=now_ts,
        )

        def _schedule_next_failure(error_text: str):
            if _pause_instance_if_needed(instance_name, error_text, source="pending_retry"):
                pause_until_local = state_store.get_mastodon_instance_pause_until(
                    instance_name,
                    consumer="mastodon_bot",
                    now_ts=int(time_module.time()),
                )
                if pause_until_local > 0:
                    state_store.schedule_failed_delivery_retry(
                        delivery_id,
                        attempt_count=int(entry.get("attempt_count", 0)),
                        next_retry_at=pause_until_local,
                        last_error=error_text,
                    )
                    return

            prev_attempt_count = int(entry.get("attempt_count", 0))
            max_retries = max(1, int(entry.get("max_retries", SEND_MAX_EXTRA_RETRIES)))
            new_attempt_count = prev_attempt_count + 1
            if new_attempt_count >= max_retries:
                state_store.mark_failed_delivery_exhausted(
                    delivery_id,
                    attempt_count=new_attempt_count,
                    last_error=error_text,
                )
                logging.error(
                    f"mastodon_bot: Retry-Job {delivery_id} ausgeschöpft "
                    f"(instanz={instance_name}, versuche={new_attempt_count}/{max_retries}): {error_text}"
                )
                return

            delay_index = min(new_attempt_count, len(SEND_RETRY_DELAYS_SECONDS) - 1)
            next_retry_at = int(time_module.time()) + SEND_RETRY_DELAYS_SECONDS[delay_index]
            state_store.schedule_failed_delivery_retry(
                delivery_id,
                attempt_count=new_attempt_count,
                next_retry_at=next_retry_at,
                last_error=error_text,
            )
            logging.warning(
                f"mastodon_bot: Retry-Job {delivery_id} erneut geplant "
                f"(instanz={instance_name}, versuche={new_attempt_count}/{max_retries}, "
                f"next_in={SEND_RETRY_DELAYS_SECONDS[delay_index]}s): {error_text}"
            )

        if pause_until > now_ts:
            state_store.schedule_failed_delivery_retry(
                delivery_id,
                attempt_count=int(entry.get("attempt_count", 0)),
                next_retry_at=pause_until,
                last_error=f"Instanz pausiert bis {pause_until}",
            )
            continue

        if not mastodon:
            _schedule_next_failure(f"Keine Mastodon-Instanz verfügbar für retry: {instance_name}")
            continue

        username = str(payload.get("username") or "")
        message = str(payload.get("message") or "")
        in_reply_to_id = payload.get("in_reply_to_id")
        quoted_status_id = payload.get("quoted_status_id")
        with_media = bool(payload.get("with_media"))
        use_video = bool(payload.get("use_video"))
        images = payload.get("images") if isinstance(payload.get("images"), list) else []
        videos = payload.get("videos") if isinstance(payload.get("videos"), list) else []
        original_tweet_full = str(payload.get("original_tweet_full") or "")
        tweet_url = str(payload.get("tweet_url") or "")
        idx = int(payload.get("idx", 0) or 0)
        status_id_raw = str(payload.get("status_id_raw") or "")

        attach_media = []
        image_payloads = None
        try:
            if with_media:
                if use_video and videos:
                    video_payload = await prepare_video_payload(
                        video_url=str(videos[0]),
                        tweet_text=original_tweet_full,
                        twitter_account=username,
                        tweet_url=tweet_url,
                    )
                    if video_payload:
                        attach_media = [video_payload]
                if not attach_media and images:
                    image_payloads = await prepare_media_payloads(
                        images=images,
                        original_tweet_full=original_tweet_full,
                        twitter_account=username,
                        tweet_url=tweet_url,
                    )
                    attach_media = image_payloads or []
        except Exception as exc:
            _schedule_next_failure(f"Fehler beim Wiederaufbereiten von Medien: {exc}")
            continue

        if with_media and not attach_media:
            _schedule_next_failure("Retry-Posting erwartet Medien, konnte aber keine Medien aufbereiten.")
            continue

        try:
            status_obj, _ = await _post_with_media_fallbacks(
                mastodon=mastodon,
                message=message,
                attach_media=attach_media,
                instance_name=instance_name,
                username=username,
                in_reply_to_id=in_reply_to_id,
                quoted_status_id=quoted_status_id if isinstance(quoted_status_id, str) else None,
                use_video=use_video,
                images=images,
                image_payloads=image_payloads,
                original_tweet_full=original_tweet_full,
                tweet_url=tweet_url,
            )
        except Exception as exc:
            _schedule_next_failure(f"Retry-Posting-Ausnahme: {exc}")
            continue

        if not status_obj:
            _schedule_next_failure("Retry-Posting lieferte kein Status-Objekt.")
            continue

        state_store.remove_failed_delivery(delivery_id)
        logging.info(f"mastodon_bot: Retry-Job {delivery_id} erfolgreich (instanz={instance_name}).")

        if idx == 0 and status_id_raw:
            try:
                status_url = status_obj.get("url") if hasattr(status_obj, "get") else getattr(status_obj, "url", "")
            except Exception:
                status_url = ""
            try:
                created_at_field = status_obj.get("created_at") if hasattr(status_obj, "get") else getattr(status_obj, "created_at", None)
            except Exception:
                created_at_field = None
            created_at_ts = int(created_at_field.timestamp()) if isinstance(created_at_field, datetime) else None
            if status_url:
                mastodon_post_store.store_post(
                    instance=instance_name,
                    status_id=status_id_raw,
                    url=status_url,
                    created_at_ts=created_at_ts,
                )
        asyncio.create_task(notify_control_bot(instance_name, status_obj, original_tweet_full))


async def main(new_tweets, thread: bool = False):
    print("Entering main function")

    mastodon_post_store.init_db()
    mastodon_post_store.prune_expired()

    clients = []
    for instance_name, instance in instances.items():
        api_base_url = instance.get('api_base_url')
        token_env_name = instance.get('access_token_env')

        access_token = os.environ.get(token_env_name)
        if not access_token:
            logging.error(f"mastodon_bot: Kein Access-Token in ENV '{token_env_name}' für {instance_name}")
            continue

        try:
            mastodon = Mastodon(access_token=access_token, api_base_url=api_base_url)
            print(f"Created Mastodon object for {instance_name}")
            clients.append((instance_name, mastodon))
        except Exception as e:
            logging.error(f"mastodon_bot: Fehler beim Erstellen des Mastodon-Objekts für {instance_name}: {e}")
            continue

    if not clients:
        logging.error("mastodon_bot: Keine Mastodon-Instanz verfügbar.")
        return

    state_store.prune_mastodon_instance_pauses()
    state_store.prune_failed_deliveries()
    await _process_pending_mastodon_retries(clients)

    instance_versions: dict[str, str] = {}
    for instance_name, mastodon in clients:
        if state_store.get_mastodon_instance_pause_until(
            instance_name,
            consumer="mastodon_bot",
            now_ts=int(time_module.time()),
        ) > 0:
            logging.info(f"mastodon_bot: Überspringe Versionsabfrage für pausierte Instanz {instance_name}.")
            continue
        version, _ = await _ensure_instance_version(
            instance_name,
            mastodon,
            min_required=MASTODON_QUOTE_MIN_VERSION,
        )
        if version:
            instance_versions[instance_name] = version

    reply_context = {name: None for name, _ in clients} if thread else None

    for tweet in new_tweets:
        try:
            username = tweet.get('username', '') or ""
            content_raw = tweet.get('content', '') or ""  # Original-Tweet unverändert

            posted_time = tweet.get('posted_time', '') or ""
            var_href = tweet.get('var_href', '') or ""     # Tweet-URL (Quelle)
            status_id_raw = (tweet.get('status_id', '') or "").strip()
            if not status_id_raw:
                status_id_raw = _extract_status_id_from_url(var_href)
            extern_urls = tweet.get('extern_urls', '') or ""
            images = tweet.get('images', []) or []
            videos = tweet.get('videos', []) or []
            quote_refs = tweet.get('quote_refs') or []
            image_payloads = None

            if isinstance(extern_urls, list):
                extern_urls = "\n".join([str(x) for x in extern_urls if x])

            _hashtags = extract_hashtags(content_raw, username)

            messages = build_mastodon_messages(
                username=username,
                tweet_text=content_raw,
                var_href=var_href,
                extern_urls=extern_urls,
                posted_time=posted_time,
                max_len=MASTODON_MAX_CHARS
            )

            messages = filter_short_mastodon_messages(
                messages=messages,
                username=username,
                min_len=MASTODON_MIN_CONTENT_LEN,
                keep_all_parts=True,
            )

            if not messages:
                logging.warning(f"mastodon_bot: Kein Mastodon-Post für {username} (Mindestlänge unterschritten).")
                continue

            media_payloads = []
            use_video = False
            if videos:
                # Versuche erstes Video, fallbacks auf Bilder
                video_url = videos[0]
                media_payload = await prepare_video_payload(
                    video_url=video_url,
                    tweet_text=content_raw,
                    twitter_account=username,
                    tweet_url=var_href
                )
                if media_payload:
                    media_payloads = [media_payload]
                    use_video = True
            if not media_payloads and images:
                image_payloads = await prepare_media_payloads(
                    images=images,
                    original_tweet_full=content_raw,
                    twitter_account=username,
                    tweet_url=var_href
                )
                media_payloads = image_payloads or []

            for instance_name, mastodon in clients:
                version = instance_versions.get(instance_name)
                quote_policy = INSTANCE_QUOTE_POLICIES.get(instance_name)
                supports_quote = False
                try:
                    supports_quote = _supports_official_quotes_with_policy(version, quote_policy)
                except Exception as exc:
                    logging.error(f"mastodon_bot: Fehler bei Versionsprüfung (instanz={instance_name}): {exc}")
                if instance_name in QUOTE_POST_UNSUPPORTED_INSTANCES and supports_quote:
                    supports_quote = False
                    logging.warning(
                        f"mastodon_bot: Instanz {instance_name} ist für Quotes gesperrt, nutze Link-Fallback."
                    )
                if quote_policy and str(quote_policy).strip().lower() in QUOTE_POLICY_DISABLED_VALUES:
                    supports_quote = False
                    logging.warning(
                        f"mastodon_bot: Quote-Policy auf {instance_name} verbietet Quotes ({quote_policy}), nutze Link-Fallback."
                    )

                quote_url_for_instance = None
                quoted_status_id_for_instance = None
                if quote_refs:
                    try:
                        quote_url_for_instance = _find_quote_url_for_instance(quote_refs, instance_name)
                    except Exception as exc:
                        logging.error(f"mastodon_bot: Fehler beim Quote-Lookup (instanz={instance_name}): {exc}")
                        quote_url_for_instance = None

                if supports_quote and quote_url_for_instance:
                    try:
                        quoted_status_id_for_instance = _extract_status_id_from_url(quote_url_for_instance)
                    except Exception as exc:
                        logging.error(f"mastodon_bot: Fehler beim Ermitteln der Quote-ID (instanz={instance_name}): {exc}")
                        quoted_status_id_for_instance = None

                if supports_quote and quoted_status_id_for_instance:
                    # Case 1: Offizielles Quote – keine „Zitat: …“-Blöcke nötig.
                    instance_messages = _replace_quote_links_for_instance(messages, quote_refs, instance_name)
                elif quote_url_for_instance:
                    # Case 2: Kein offizielles Quote, aber Vorgänger im Store -> Link-Fallback.
                    instance_messages = _replace_quote_links_for_instance(messages, quote_refs, instance_name)
                    if supports_quote and not quoted_status_id_for_instance:
                        logging.warning(
                            f"mastodon_bot: Quote-ID nicht gefunden im Store (instanz={instance_name}, status={status_id_raw}), nutze Link-Fallback"
                        )
                    elif not supports_quote:
                        logging.info(
                            f"mastodon_bot: Instanz {instance_name} unterstützt keine offiziellen Quotes (Version={version}), nutze Link-Fallback"
                        )
                else:
                    # Case 3: Kein Eintrag im Store -> „Zitat: …“ stehen lassen, damit der Leser den Verweis sieht.
                    instance_messages = messages
                    if quote_refs:
                        logging.warning(
                            f"mastodon_bot: Quote nicht im Store gefunden (instanz={instance_name}, status={status_id_raw}), sende ohne Quote."
                        )

                pause_until = state_store.get_mastodon_instance_pause_until(
                    instance_name,
                    consumer="mastodon_bot",
                    now_ts=int(time_module.time()),
                )
                if pause_until > 0:
                    pause_until_dt = datetime.fromtimestamp(pause_until, BERLIN_TZ).strftime("%Y-%m-%d %H:%M:%S %Z")
                    logging.warning(
                        f"mastodon_bot: Instanz {instance_name} ist pausiert bis {pause_until_dt}; "
                        "poste nicht direkt und lege Beiträge in die Warteschlange."
                    )
                    base_reply_to_id = reply_context.get(instance_name) if reply_context is not None else None
                    for idx, queued_message in enumerate(instance_messages):
                        queued_attach_media = media_payloads if idx == 0 else []
                        queued_quote_id = quoted_status_id_for_instance if supports_quote and idx == 0 else None
                        queued_payload = _build_mastodon_retry_payload(
                            instance_name=instance_name,
                            username=username,
                            message=queued_message,
                            in_reply_to_id=base_reply_to_id,
                            quoted_status_id=queued_quote_id,
                            with_media=bool(queued_attach_media),
                            use_video=bool(use_video and idx == 0),
                            images=images if idx == 0 else [],
                            videos=videos if idx == 0 else [],
                            original_tweet_full=content_raw,
                            tweet_url=var_href,
                            idx=idx,
                            status_id_raw=status_id_raw,
                        )
                        _enqueue_mastodon_retry(
                            queued_payload,
                            error_text=(
                                f"Instanz pausiert bis {pause_until_dt}; "
                                f"Posting zurückgestellt (instanz={instance_name}, user={username}, idx={idx}, url={var_href})"
                            ),
                        )
                    continue
                reply_to_id = reply_context.get(instance_name) if reply_context is not None else None
                last_status_id = None
                quote_link_messages = None

                for idx, message in enumerate(instance_messages):
                    attach_media = media_payloads if idx == 0 else []
                    quote_for_post = quoted_status_id_for_instance if supports_quote and idx == 0 else None

                    if attach_media:
                        print(f"Posting tweet with media for {username} to {instance_name}")
                    else:
                        print(f"Posting tweet without images for {username} to {instance_name}")

                    status_obj, image_payloads = await _post_with_media_fallbacks(
                        mastodon=mastodon,
                        message=message,
                        attach_media=attach_media,
                        instance_name=instance_name,
                        username=username,
                        in_reply_to_id=reply_to_id,
                        quoted_status_id=quote_for_post,
                        use_video=use_video,
                        images=images,
                        image_payloads=image_payloads,
                        original_tweet_full=content_raw,
                        tweet_url=var_href,
                    )

                    if not status_obj and quote_for_post and quote_url_for_instance:
                        if quote_link_messages is None:
                            try:
                                quote_link_messages = _replace_quote_links_for_instance(
                                    messages, quote_refs, instance_name
                                )
                            except Exception as exc:
                                logging.error(
                                    f"mastodon_bot: Fehler beim Erstellen des Quote-Link-Fallbacks (instanz={instance_name}): {exc}"
                                )
                                quote_link_messages = []

                        fallback_message = None
                        if quote_link_messages:
                            fallback_message = (
                                quote_link_messages[idx] if idx < len(quote_link_messages) else quote_link_messages[0]
                            )

                        if fallback_message:
                            logging.warning(
                                f"mastodon_bot: Offizielle Quote fehlgeschlagen (instanz={instance_name}, user={username}), versuche Link-Fallback"
                            )
                            status_obj, image_payloads = await _post_with_media_fallbacks(
                                mastodon=mastodon,
                                message=fallback_message,
                                attach_media=attach_media,
                                instance_name=instance_name,
                                username=username,
                                in_reply_to_id=reply_to_id,
                                quoted_status_id=None,
                                use_video=use_video,
                                images=images,
                                image_payloads=image_payloads,
                                original_tweet_full=content_raw,
                                tweet_url=var_href,
                            )

                    if not status_obj:
                        retry_payload = _build_mastodon_retry_payload(
                            instance_name=instance_name,
                            username=username,
                            message=message,
                            in_reply_to_id=reply_to_id,
                            quoted_status_id=quote_for_post,
                            with_media=bool(attach_media),
                            use_video=bool(use_video and idx == 0),
                            images=images if idx == 0 else [],
                            videos=videos if idx == 0 else [],
                            original_tweet_full=content_raw,
                            tweet_url=var_href,
                            idx=idx,
                            status_id_raw=status_id_raw,
                        )
                        _enqueue_mastodon_retry(
                            retry_payload,
                            error_text=(
                                f"Posting fehlgeschlagen (instanz={instance_name}, user={username}, "
                                f"idx={idx}, url={var_href})"
                            ),
                        )
                        pause_until_after_failure = state_store.get_mastodon_instance_pause_until(
                            instance_name,
                            consumer="mastodon_bot",
                            now_ts=int(time_module.time()),
                        )
                        if pause_until_after_failure > 0:
                            for queued_idx in range(idx + 1, len(instance_messages)):
                                tail_message = instance_messages[queued_idx]
                                tail_payload = _build_mastodon_retry_payload(
                                    instance_name=instance_name,
                                    username=username,
                                    message=tail_message,
                                    in_reply_to_id=reply_to_id,
                                    quoted_status_id=None,
                                    with_media=False,
                                    use_video=False,
                                    images=[],
                                    videos=[],
                                    original_tweet_full=content_raw,
                                    tweet_url=var_href,
                                    idx=queued_idx,
                                    status_id_raw=status_id_raw,
                                )
                                _enqueue_mastodon_retry(
                                    tail_payload,
                                    error_text=(
                                        f"Instanz pausiert nach Fehler; Restbeitrag zurückgestellt "
                                        f"(instanz={instance_name}, user={username}, idx={queued_idx}, url={var_href})"
                                    ),
                                )
                        logging.error(
                            f"mastodon_bot: Posting fehlgeschlagen (instanz={instance_name}, user={username}, idx={idx}, url={var_href})"
                        )
                        break

                    if idx == 0 and status_id_raw:
                        try:
                            status_url = status_obj.get("url") if hasattr(status_obj, "get") else getattr(status_obj, "url", "")
                        except Exception:
                            status_url = ""
                        try:
                            created_at_field = status_obj.get("created_at") if hasattr(status_obj, "get") else getattr(status_obj, "created_at", None)
                        except Exception:
                            created_at_field = None
                        created_at_ts = None
                        if isinstance(created_at_field, datetime):
                            created_at_ts = int(created_at_field.timestamp())

                        if status_url:
                            mastodon_post_store.store_post(
                                instance=instance_name,
                                status_id=status_id_raw,
                                url=status_url,
                                created_at_ts=created_at_ts,
                            )

                    asyncio.create_task(notify_control_bot(instance_name, status_obj, content_raw))
                    status_id = status_obj.get("id") if hasattr(status_obj, "get") else getattr(status_obj, "id", None)
                    if status_id:
                        reply_to_id = status_id
                        last_status_id = status_id

                if reply_context is not None and last_status_id:
                    reply_context[instance_name] = last_status_id
        except Exception as e:
            logging.error(f"mastodon_bot: Unerwarteter Fehler in Tweet-Loop (url={tweet.get('var_href', '')}, user={tweet.get('username', '')}): {e}")
            continue

    print("Main function completed")


print("Script loaded")
