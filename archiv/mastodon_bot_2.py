import asyncio
import os
import re
import logging
from datetime import date, datetime, time, timedelta
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

# Initialize the Google Gemini client with the API key
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
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
print("Logging configured")

# Pfad für Tagging-Regeln (wird vom Control-Bot gepflegt)
BERLIN_TZ = ZoneInfo("Europe/Berlin")
TAG_DM_MAX = 440


def log_alt_warning(msg: str):
    logging.warning(f"{ALT_TEXT_LOG_PREFIX}: {msg}")


def log_alt_error(msg: str):
    logging.error(f"{ALT_TEXT_LOG_PREFIX}: {msg}")

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
        return await asyncio.to_thread(
            _status_post_with_quote_support,
            mastodon=mastodon,
            message=message,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id,
            media_ids=None,
            quoted_status_id=quoted_status_id,
        )
    except Exception as e:
        if _is_quote_feature_error(e):
            _mark_quote_feature_unsupported(instance_name, str(e))
            logging.warning(
                f"mastodon_bot: Quote-API fehlt auf {instance_name}, poste ohne offizielle Quote (text-only)."
            )
            return None
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
    try:
        async with session.get(url) as response:
            if response.status == 200:
                return await response.read()
            logging.error(f"mastodon_bot: Fehler beim Herunterladen des Bildes: {url}")
            return None
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler beim Herunterladen des Bildes: {e}")
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

    def _is_quota_error(msg: str) -> bool:
        m = msg.lower()
        return "resource_exhausted" in m or "quota" in m or "exceeded your current quota" in m or "429" in m

    for model_name in gemini_manager.get_candidate_models():
        try:
            with Image.open(io.BytesIO(image_bytes)) as img:
                response = client.models.generate_content(
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
                log_alt_warning(f"Modell '{model_name}' schlug fehl: {msg}")
                helper_logger.warning(f"{GEMINI_HELPER_PREFIX}: Modell '{model_name}' schlug fehl: {msg}")
                gemini_manager.mark_failed(model_name, msg)
                continue

            gemini_manager.mark_success(model_name)
            return text[:1500]
        except Exception as e:
            err_txt = str(e)
            if "not found" in err_txt.lower() or "404" in err_txt:
                helper_logger.warning(f"{GEMINI_HELPER_PREFIX}: Modell '{model_name}' schlug fehl: {err_txt}")
                log_alt_warning(f"Modell '{model_name}' schlug fehl: {err_txt}")
                gemini_manager.mark_not_found(model_name, err_txt)
                continue

            if _is_quota_error(err_txt):
                log_alt_warning(f"Modell '{model_name}' schlug fehl: {err_txt}")
                helper_logger.warning(f"{GEMINI_HELPER_PREFIX}: Modell '{model_name}' schlug fehl: {err_txt}")
                gemini_manager.mark_quota(model_name, err_txt)
                continue

            log_alt_error(f"Modell '{model_name}' schlug fehl: {err_txt}")
            helper_logger.warning(f"{GEMINI_HELPER_PREFIX}: Modell '{model_name}' schlug fehl: {err_txt}")
            gemini_manager.mark_failed(model_name, err_txt)
            continue

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
                        client=client,
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


async def upload_media_payloads(mastodon, media_payloads):
    media_ids = []
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
            logging.error(f"mastodon_bot: Fehler beim Media-Post: {e}")
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
            media_payloads=media_payloads
        )
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler beim Hochladen der Medien: {e}")
        return

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

        return await asyncio.to_thread(
            _status_post_with_quote_support,
            mastodon=mastodon,
            message=message,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id,
            media_ids=media_ids,
            quoted_status_id=quoted_status_id,
        )

    except Exception as e:
        if _is_quote_feature_error(e):
            _mark_quote_feature_unsupported(instance_name, str(e))
            logging.warning(
                f"mastodon_bot: Quote-API fehlt auf {instance_name}, poste ohne offizielle Quote (media)."
            )
            return None
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
            media_payloads=media_payloads
        )
    except Exception as e:
        logging.error(f"mastodon_bot: Fallback-Fehler beim Hochladen der Bilder: {e}")
        return

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

        return await asyncio.to_thread(
            _status_post_with_quote_support,
            mastodon=mastodon,
            message=message,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id,
            media_ids=media_ids,
            quoted_status_id=quoted_status_id,
        )
    except Exception as e:
        if _is_quote_feature_error(e):
            _mark_quote_feature_unsupported(instance_name, str(e))
            logging.warning(
                f"mastodon_bot: Quote-API fehlt auf {instance_name}, poste ohne offizielle Quote (legacy media)."
            )
            return None
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

    instance_versions: dict[str, str] = {}
    for instance_name, mastodon in clients:
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
                        logging.warning(
                            f"mastodon_bot: Instanz {instance_name} unterstützt keine offiziellen Quotes (Version={version}), nutze Link-Fallback"
                        )
                else:
                    # Case 3: Kein Eintrag im Store -> „Zitat: …“ stehen lassen, damit der Leser den Verweis sieht.
                    instance_messages = messages
                    if quote_refs:
                        logging.warning(
                            f"mastodon_bot: Quote nicht im Store gefunden (instanz={instance_name}, status={status_id_raw}), sende ohne Quote."
                        )
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
