#!/usr/bin/env python3

import argparse
import asyncio
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, time as dtime
from urllib.parse import parse_qs, unquote, urlparse

import feedparser
from dateutil.parser import parse
import pytz
import requests

import telegram_bot
import mastodon_bot
import state_store

BASE_DIR = os.environ.get("BOTS_BASE_DIR", "/home/sascha/bots")
LOG_PATH = os.path.join(BASE_DIR, "twitter_bot.log")
POLL_INTERVAL = int(os.environ.get("NITTER_POLL_INTERVAL", "60"))
HISTORY_LIMIT = int(os.environ.get("NITTER_HISTORY_LIMIT", "200"))
MAX_ITEM_AGE_SECONDS = max(
    0, int(os.environ.get("NITTER_MAX_ITEM_AGE_SECONDS", str(2 * 60 * 60)))
)

DEFAULT_INTERVAL = 15 * 60  # Sekunden
USER_OVERRIDES = {
    "sbahnberlin": {
        "interval": 2 * 60,
        "active_start": "05:55",
        "active_end": "22:05",
    }
}
DEFAULT_USER_CONFIGS = [
    {"username": "SBahnBerlin", "interval_seconds": 120, "active_start": "05:55", "active_end": "22:05"},
    {"username": "bpol_11", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "DB_Info", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "DBRegio_BB", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "ViP_Potsdam", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "bpol_b_einsatz", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "bpol_b", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "PolizeiBerlin_E", "interval_seconds": 900, "active_start": "", "active_end": ""},
    {"username": "Berliner_Fw", "interval_seconds": 900, "active_start": "", "active_end": ""},
]

BERLIN_TZ = pytz.timezone("Europe/Berlin")

NITTER_BASE_URL = os.environ.get("NITTER_BASE_URL", "http://localhost:8081").rstrip("/")

_base_netloc = urlparse(NITTER_BASE_URL).netloc or urlparse(NITTER_BASE_URL).path
_base_host = _base_netloc.split(":")[0]
_base_scheme = urlparse(NITTER_BASE_URL).scheme or "http"
INVIDIOUS_ENABLED_DEFAULT = False  # Invidious-Umschreibung aktivieren/deaktivieren
INVIDIOUS_BASE_URL_DEFAULT = ""  # z. B. "https://yewtu.be"
INVIDIOUS_ENABLED = (
    os.environ.get("INVIDIOUS_ENABLED", "true" if INVIDIOUS_ENABLED_DEFAULT else "false")
    .strip()
    .lower()
    in {"1", "true", "yes", "on"}
)
INVIDIOUS_BASE_URL = os.environ.get("INVIDIOUS_BASE_URL", INVIDIOUS_BASE_URL_DEFAULT).strip().rstrip("/")
if INVIDIOUS_BASE_URL and not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", INVIDIOUS_BASE_URL):
    INVIDIOUS_BASE_URL = "https://" + INVIDIOUS_BASE_URL
INTERNAL_HOSTS = {h for h in (_base_netloc, _base_host, "localhost", "127.0.0.1") if h}

URL_RE = re.compile(r"https?://[^\s<>\"']+")
# Erfasst http/https, www., sowie nackte Domains mit optionalem Pfad
TEXT_URL_RX = re.compile(
    r"(?P<url>(?:https?://|www\.)[^\s]+|(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}(?:/[^\s]*)?)",
    re.IGNORECASE,
)
MENTION_RE = re.compile(r"(?<!\w)@([A-Za-z0-9_]{1,30})")

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)

def parse_time(value: str | None) -> dtime | None:
    if not value:
        return None
    try:
        hh, mm = value.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        logging.error(f"nitter_bot: Ungueltige Zeitangabe '{value}', erwartet HH:MM")
        return None


def _generate_default_users_map() -> dict[str, dict]:
    defaults: dict[str, dict] = {}
    for cfg in DEFAULT_USER_CONFIGS:
        username = (cfg.get("username") or "").strip()
        if not username:
            continue
        try:
            interval = int(cfg.get("interval_seconds") or DEFAULT_INTERVAL)
        except Exception:
            interval = DEFAULT_INTERVAL
        override = USER_OVERRIDES.get(username.lower(), {})
        defaults[username] = {
            "interval_seconds": override.get("interval", interval),
            "active_start": override.get("active_start", cfg.get("active_start", "")),
            "active_end": override.get("active_end", cfg.get("active_end", "")),
        }
    return defaults


def _seed_default_users() -> dict[str, dict]:
    defaults = _generate_default_users_map()
    state_store.save_nitter_users(defaults)
    return defaults


def build_user_configs(persist: bool = True):
    stored = state_store.load_nitter_users()
    if not stored:
        stored = _seed_default_users() if persist else _generate_default_users_map()

    configs = []
    for username, cfg in (stored or {}).items():
        uname = (username or "").strip()
        if not uname:
            continue
        cfg = cfg if isinstance(cfg, dict) else {}
        try:
            interval = int(cfg.get("interval_seconds") or cfg.get("interval") or DEFAULT_INTERVAL)
        except Exception:
            interval = DEFAULT_INTERVAL
        if interval <= 0:
            interval = DEFAULT_INTERVAL

        active_start = parse_time((cfg.get("active_start") or "").strip() or None)
        active_end = parse_time((cfg.get("active_end") or "").strip() or None)

        configs.append(
            {
                "username": uname,
                "interval": interval,
                "active_start": active_start,
                "active_end": active_end,
                "next_run": 0.0,
            }
        )

    return configs


def _is_within_window(now: datetime, start: dtime | None, end: dtime | None) -> tuple[bool, float | None]:
    if not start or not end:
        return True, None

    today = now.date()
    start_dt = datetime.combine(today, start, tzinfo=BERLIN_TZ)
    end_dt = datetime.combine(today, end, tzinfo=BERLIN_TZ)
    if end_dt <= start_dt:
        end_dt += timedelta(days=1)

    if start_dt <= now <= end_dt:
        return True, None

    if now < start_dt:
        return False, start_dt.timestamp()
    next_start = start_dt + timedelta(days=1)
    return False, next_start.timestamp()


def dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def extract_status_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = re.search(r"/status/(\d+)", url)
    if match:
        return match.group(1)
    fallback = re.findall(r"\d{5,}", url)
    return fallback[0] if fallback else ""


def sanitize_status_id(value: str) -> str:
    """
    Extrahiert eine numerische Status-ID, auch wenn der Wert ein vollständiger Link ist.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        return raw
    extracted = extract_status_id_from_url(raw)
    return extracted or ""


def clean_history_map(
    history_map: dict[str, list[str]], per_user_limits: dict[str, int] | None = None
) -> tuple[dict[str, list[str]], set[str], bool]:
    """
    Entfernt leere Einträge, dedupliziert und kürzt die History pro Nutzer.
    Gibt die bereinigte Map, die rekonstruierten Status-IDs sowie ein Flag zurück,
    das signalisiert, ob eine Änderung vorgenommen wurde.
    """
    cleaned_map: dict[str, list[str]] = {}
    seen_ids: set[str] = set()
    changed = False

    for user, entries in (history_map or {}).items():
        if not isinstance(entries, list):
            continue

        normalized_entries = dedupe_preserve_order(
            [
                (entry or "").strip()
                for entry in entries
                if isinstance(entry, str) and (entry or "").strip()
            ]
        )

        limit = HISTORY_LIMIT
        if per_user_limits:
            try:
                limit = max(1, int(per_user_limits.get(user, HISTORY_LIMIT)))
            except Exception:
                limit = HISTORY_LIMIT

        trimmed_entries = normalized_entries[-limit:]
        if trimmed_entries:
            cleaned_map[str(user)] = trimmed_entries
            for entry in trimmed_entries:
                status_id = extract_status_id_from_url(entry)
                if status_id:
                    seen_ids.add(status_id)

        if trimmed_entries != entries:
            changed = True

    return cleaned_map, seen_ids, changed


def add_port_if_local(url: str) -> str:
    try:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path
        if not host:
            return url
        host_no_port = host.split(":")[0]
        has_port = ":" in host and host_no_port != host
        if host_no_port in INTERNAL_HOSTS and not has_port:
            return parsed._replace(scheme=_base_scheme, netloc=_base_netloc).geturl()
        return url
    except Exception:
        return url


def normalize_youtube_url(url: str) -> str:
    try:
        parsed = urlparse(url)
    except Exception:
        return url

    netloc = (parsed.netloc or "").lower()
    query = parse_qs(parsed.query, keep_blank_values=True)
    path_parts = [p for p in parsed.path.split("/") if p]

    if netloc == "consent.youtube.com":
        cont_values = query.get("continue") or []
        if cont_values:
            target = unquote(cont_values[0] or "")
            if target.startswith("//"):
                target = "https:" + target
            return normalize_youtube_url(target)
        return url

    if netloc in {"piped.video", "www.piped.video"}:
        netloc = "www.youtube.com"
        parsed = parsed._replace(scheme="https", netloc=netloc)
        path_parts = [p for p in parsed.path.split("/") if p]
        query = parse_qs(parsed.query, keep_blank_values=True)

    if netloc in {"youtu.be", "www.youtu.be"}:
        video_id = path_parts[0] if path_parts else ""
        return f"https://www.youtube.com/watch?v={video_id}" if video_id else "https://www.youtube.com"

    if netloc.endswith("youtube.com"):
        video_id = None
        is_live = False
        if path_parts[:1] == ["watch"]:
            video_id = (query.get("v") or [None])[0]
        elif path_parts[:1] == ["live"] and len(path_parts) >= 2:
            video_id = path_parts[1]
            is_live = True
        elif path_parts[:1] == ["shorts"] and len(path_parts) >= 2:
            video_id = path_parts[1]
        elif path_parts[:1] == ["embed"] and len(path_parts) >= 2:
            video_id = path_parts[1]
        elif len(path_parts) >= 2 and path_parts[0] == "v":
            video_id = path_parts[1]

        if video_id:
            video_id = video_id.strip()
            if video_id:
                if is_live:
                    return f"https://www.youtube.com/live/{video_id}"
                return f"https://www.youtube.com/watch?v={video_id}"

    return url


def normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    cleaned = cleaned.strip(".,;:!?()[]{}<>\"'…")
    cleaned = cleaned.replace("%E2%80%A6", "")
    if not cleaned:
        return ""
    cleaned = re.sub(r"^https:/(?!/)", "https://", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"^http:/(?!/)", "http://", cleaned, flags=re.IGNORECASE)
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = cleaned.lstrip("/")
        cleaned = f"https://{cleaned}"
    if cleaned.startswith("http://"):
        cleaned = "https://" + cleaned[len("http://") :]
    cleaned = normalize_youtube_url(cleaned)
    return cleaned


def replace_with_invidious(url: str) -> str:
    """
    Optional: ersetzt youtube.com durch konfigurierten Invidious-Host, falls gesetzt.
    """
    if not INVIDIOUS_ENABLED or not INVIDIOUS_BASE_URL:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.netloc or parsed.path).lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            return url

        inv = urlparse(INVIDIOUS_BASE_URL)
        inv_netloc = inv.netloc or inv.path
        if not inv_netloc:
            return url
        scheme = inv.scheme or "https"
        return parsed._replace(scheme=scheme, netloc=inv_netloc).geturl()
    except Exception:
        return url


def expand_short_urls(urls: list[str]) -> list[str]:
    expanded_urls = []
    seen: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; nitter-bot/1.0)"}
    for raw in urls:
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        response = None
        try:
            response = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
            status_ok = response is not None and 200 <= response.status_code < 400
            status_code = getattr(response, "status_code", None)

            # Einige Server liefern bei HEAD (z. B. 400/405) falsche Fehler; dann auf GET ausweichen.
            should_retry_get = (not status_ok) and status_code not in (429,)
            if ("dlvr.it" in url.lower()) and not status_ok:
                should_retry_get = True

            if should_retry_get:
                if response is not None:
                    response.close()
                response = requests.get(
                    url, allow_redirects=True, timeout=8, headers=headers, stream=True
                )
                status_ok = response is not None and 200 <= response.status_code < 400

            if status_ok and response is not None:
                expanded_urls.append(response.url)
            else:
                status_code = getattr(response, "status_code", "unknown")
                log_fn = logging.warning
                if isinstance(status_code, int) and status_code >= 500:
                    log_fn = logging.error
                log_fn(f"nitter_bot: Überprüfung URL {url} liefert ungültigen Status {status_code}")
        except Exception as ex:
            logging.error(f"nitter_bot: Fehler beim Überprüfen der URL {url}: {ex}")
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass

    return dedupe_preserve_order([normalize_url(u) for u in expanded_urls])


def remove_truncated_url_tokens(text: str) -> str:
    """
    Entfernt Wort-Tokens, die wie abgeschnittene URLs aussehen (… oder ... mit / oder .).
    """
    if not text:
        return ""
    tokens = text.split()
    cleaned = []
    for tok in tokens:
        has_ellipsis = "…" in tok or "..." in tok
        looks_like_url = "/" in tok or "." in tok or tok.lower().startswith(("http", "www"))
        if has_ellipsis and looks_like_url:
            continue
        cleaned.append(tok)
    return " ".join(cleaned)


def replace_mentions_with_hash(text: str) -> str:
    """
    Ersetzt @handles durch #handles, ohne E-Mail-Adressen (kein Wortzeichen davor).
    """
    if not text:
        return ""
    return MENTION_RE.sub(r"#\1", text)


def strip_urls_from_text(text: str, known_urls: list[str] | None = None) -> str:
    """
    Entfernt alle erkannten URLs (auch ohne Schema) aus dem Text.
    known_urls werden zusätzlich (inkl. expandierter Kurzlinks) entfernt.
    """
    if not text:
        return ""

    def _replacer(match):
        return ""

    cleaned = TEXT_URL_RX.sub(_replacer, text)

    if known_urls:
        variants: list[str] = []
        for raw in known_urls:
            if not raw:
                continue
            normalized = normalize_url(raw)
            variants.extend([raw, normalized])
            try:
                parsed = urlparse(normalized)
                host_path = (parsed.netloc + parsed.path).lstrip("/")
                if host_path:
                    variants.append(host_path)
            except Exception:
                pass

        for cand in dedupe_preserve_order([v for v in variants if v]):
            if not cand:
                continue
            cleaned = cleaned.replace(cand, "")

    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip()


def load_history(persist: bool = True) -> tuple[dict[str, list[str]], set[str], bool]:
    """
    Lädt History als Mapping pro Nutzer. Unterstützt Legacy-Format (Plaintext-Zeilen).
    Gibt zusätzlich zurück, ob beim Bereinigen Änderungen vorgenommen wurden.
    """
    history_map: dict[str, list[str]] = {}
    raw = state_store.load_nitter_history()
    if raw and isinstance(raw, dict):
        for user, entries in raw.items():
            if not isinstance(entries, list):
                continue
            normalized_entries = []
            for entry in entries:
                if not isinstance(entry, str):
                    continue
                entry = entry.strip()
                if not entry:
                    continue
                normalized_entries.append(entry)
            if normalized_entries:
                history_map[str(user)] = normalized_entries

    cleaned_map, seen_ids, changed = clean_history_map(history_map, None)
    if changed and persist:
        try:
            state_store.save_nitter_history(cleaned_map)
        except Exception as exc:
            logging.error(f"nitter_bot: Fehler beim Speichern der bereinigten History: {exc}")
    return cleaned_map, seen_ids, changed


def save_history(
    history_map: dict[str, list[str]], per_user_limits: dict[str, int] | None = None
) -> dict[str, list[str]]:
    """
    Speichert History pro Nutzer als JSON, pro User auf Limit begrenzt.
    Gibt die bereinigte History zurück.
    """
    trimmed_map, _, _ = clean_history_map(history_map, per_user_limits)

    try:
        state_store.save_nitter_history(trimmed_map)
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Schreiben der History: {exc}")

    return trimmed_map


def html_to_text(html_fragment: str) -> str:
    if not html_fragment:
        return ""
    text = html_fragment
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p>\s*<p>", "\n\n", text)
    text = re.sub(r"(?i)<hr\s*/?>", "\n", text)
    text = re.sub(r"(?i)</?(blockquote|p)>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_summary_and_quotes(summary_html: str) -> tuple[str, list[str]]:
    """
    Trennt den Hauptteil eines RSS-Summarys von allen vorhandenen Blockquotes.
    Gibt das HTML des Hauptteils sowie eine Liste der Blockquote-Inhalte (in Reihenfolge) zurück.
    """
    if not summary_html:
        return "", []

    quotes: list[str] = []

    def _collect(match):
        quotes.append(match.group(1))
        return ""

    main_html = re.sub(r"(?is)<blockquote[^>]*>(.*?)</blockquote>", _collect, summary_html).strip()
    return main_html, quotes


def extract_quote_info(quote_html: str, fallback_username: str):
    if not quote_html:
        return None

    quote_username = fallback_username
    username_match = re.search(r"@([A-Za-z0-9_]{1,30})", quote_html)
    if username_match:
        quote_username = username_match.group(1)

    status_url_raw = ""
    for href in re.findall(r'href="([^"]+)"', quote_html):
        if "/status/" in href:
            status_url_raw = add_port_if_local(href)
            break

    if status_url_raw:
        try:
            parsed = urlparse(status_url_raw)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[1] == "status" and parts[0]:
                quote_username = parts[0]
        except Exception:
            pass

    quote_status_id = extract_status_id_from_url(status_url_raw)
    canonical_quote_url = ""
    if quote_status_id:
        canonical_quote_url = build_canonical_url(quote_username, quote_status_id, status_url_raw)

    quote_text = html_to_text(quote_html)

    return {
        "text": quote_text,
        "username": quote_username,
        "status_id": quote_status_id,
        "canonical_url": canonical_quote_url,
        "raw_status_url": status_url_raw,
    }


def is_internal_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = parsed.netloc or parsed.path
        host_no_port = host.split(":")[0]
        return host in INTERNAL_HOSTS or host_no_port in INTERNAL_HOSTS
    except Exception:
        return False


def extract_urls_from_text(text: str):
    urls = []
    for match in URL_RE.finditer(text or ""):
        candidate = match.group(0).rstrip(".,)")
        if not candidate or is_internal_url(candidate):
            continue
        urls.append(add_port_if_local(candidate))
    return dedupe_preserve_order(urls)


def parse_summary(summary: str):
    text = html_to_text(summary)
    images = []
    videos = []
    extern_urls = []

    for href in re.findall(r'href="([^"]+)"', summary or ""):
        if not href or is_internal_url(href):
            continue
        if href.lower().endswith((".mp4", ".m3u8")):
            videos.append(href)
        else:
            extern_urls.append(href)

    for src in re.findall(r'src="([^"]+)"', summary or ""):
        if not src:
            continue
        if src.lower().endswith((".mp4", ".m3u8")):
            videos.append(add_port_if_local(src))
        else:
            images.append(add_port_if_local(src))

    return (
        text,
        dedupe_preserve_order(images),
        dedupe_preserve_order(videos),
        dedupe_preserve_order(extern_urls),
    )


def parse_published(entry):
    published = entry.get("published") or entry.get("updated") or ""
    posted_time = ""
    timestamp = None
    if published:
        try:
            dt = parse(published)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=pytz.utc)
            timestamp = dt.timestamp()
            posted_time = dt.astimezone(pytz.timezone("Europe/Berlin")).strftime("%d.%m.%Y %H:%M")
        except Exception:
            posted_time = published
    if timestamp is None:
        timestamp = time.time()
    return timestamp, posted_time


def build_canonical_url(username: str, status_id: str, fallback_link: str) -> str:
    if status_id and username:
        return f"https://x.com/{username}/status/{status_id}"
    if status_id:
        return f"https://x.com/i/status/{status_id}"
    return fallback_link or ""


def to_external_source_url(link: str, username: str, status_id: str) -> str:
    """
    Wandelt lokale Nitter-Links in x.com-Links um, falls möglich.
    """
    if status_id and username:
        return build_canonical_url(username, status_id, "")

    extracted_id = extract_status_id_from_url(link)
    user_candidate = (username or "").strip()

    if not user_candidate:
        try:
            parsed = urlparse(link)
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[1] == "status":
                user_candidate = parts[0]
        except Exception:
            user_candidate = ""

    if extracted_id and user_candidate:
        return build_canonical_url(user_candidate, extracted_id, "")

    if extracted_id:
        return build_canonical_url("", extracted_id, link)

    return link


def get_basic_entry(entry, feed_username: str):
    status_id_raw = str(entry.get("id") or entry.get("guid") or "").strip()
    status_id = sanitize_status_id(status_id_raw)
    if not status_id:
        status_id = extract_status_id_from_url(entry.get("link", ""))

    username = (entry.get("author") or "").lstrip("@").strip() or feed_username
    published_ts, posted_time = parse_published(entry)
    link_raw = entry.get("link", "") or ""
    link_local_fixed = add_port_if_local(link_raw)
    canonical_url = build_canonical_url(username, status_id, "")
    source_url = to_external_source_url(link_local_fixed, username, status_id)

    return {
        "status_id": status_id,
        "username": username,
        "published_ts": published_ts,
        "posted_time": posted_time,
        "link_local_fixed": link_local_fixed,
        "canonical_url": canonical_url,
        "source_url": source_url,
    }


def parse_entry(entry, feed_username: str, basics: dict | None = None):
    basics = basics or get_basic_entry(entry, feed_username)
    status_id = basics.get("status_id", "")
    username = basics.get("username") or feed_username
    published_ts = basics.get("published_ts")
    posted_time = basics.get("posted_time") or ""
    link_local_fixed = basics.get("link_local_fixed") or add_port_if_local(entry.get("link", "") or "")
    canonical_url = basics.get("canonical_url") or build_canonical_url(username, status_id, "")
    source_url = basics.get("source_url") or to_external_source_url(link_local_fixed, username, status_id)

    summary_html = entry.get("summary") or entry.get("description") or ""
    title_text = html.unescape(entry.get("title", "") or "")

    main_html, quote_htmls = split_summary_and_quotes(summary_html)
    _, images, videos, extern_urls = parse_summary(summary_html)

    content_text = html_to_text(main_html)
    if not content_text:
        content_text = html_to_text(title_text)

    extern_urls.extend(extract_urls_from_text(title_text))
    extern_urls = [normalize_url(u) for u in extern_urls if u]
    extern_urls = dedupe_preserve_order(extern_urls)
    extern_urls = expand_short_urls(extern_urls)
    extern_urls = dedupe_preserve_order([normalize_url(u) for u in extern_urls])

    quote_blocks: list[str] = []
    quote_known_urls: list[str] = []
    quote_urls_for_dedupe: list[str] = []
    quote_refs: list[dict] = []
    for quote_html in quote_htmls:
        quote_info = extract_quote_info(quote_html, username)
        if not quote_info:
            continue

        quote_text_clean = quote_info.get("text") or ""
        canonical_quote_url = quote_info.get("canonical_url") or ""
        raw_quote_url = quote_info.get("raw_status_url") or ""

        if canonical_quote_url:
            quote_known_urls.append(canonical_quote_url)
            quote_urls_for_dedupe.append(canonical_quote_url)
        if raw_quote_url:
            quote_known_urls.append(raw_quote_url)
            quote_urls_for_dedupe.append(raw_quote_url)

        quote_url_display = canonical_quote_url or raw_quote_url or ""

        if quote_text_clean:
            quote_text_clean = strip_urls_from_text(quote_text_clean, quote_known_urls)
            quote_text_clean = remove_truncated_url_tokens(quote_text_clean)
            quote_text_clean = replace_mentions_with_hash(quote_text_clean)

        quote_block = quote_url_display or quote_text_clean
        if quote_block:
            quote_blocks.append(f"Zitat: {quote_block}")
            quote_refs.append(
                {
                    "status_id": quote_info.get("status_id") or "",
                    "display": quote_block,
                    "canonical_url": canonical_quote_url or "",
                }
            )

    quote_url_norms = {normalize_url(url) for url in quote_urls_for_dedupe if url}
    filtered_extern_urls: list[str] = []
    for raw_url in extern_urls:
        normalized = normalize_url(raw_url)
        if normalized and normalized not in quote_url_norms:
            filtered_extern_urls.append(normalized)
    extern_urls = dedupe_preserve_order(filtered_extern_urls)

    urls_for_stripping = extern_urls + quote_known_urls
    if content_text:
        content_text = strip_urls_from_text(content_text, urls_for_stripping)

    extern_urls = dedupe_preserve_order([replace_with_invidious(u) for u in extern_urls])
    content_text = remove_truncated_url_tokens(content_text)
    content_text = replace_mentions_with_hash(content_text)

    if quote_blocks:
        quotes_combined = "\n".join(quote_blocks)
        if content_text:
            content_text = f"{content_text}\n\n{quotes_combined}"
        else:
            content_text = quotes_combined

    return {
        "status_id": status_id,
        "username": username,
        "content": content_text,
        "posted_time": posted_time,
        "extern_urls": extern_urls,
        "images": images,
        "videos": videos,
        "link": source_url or link_local_fixed,
        "canonical_url": canonical_url or source_url,
        "published_ts": published_ts,
        "quote_refs": quote_refs,
    }


def fetch_feed(username: str):
    feed_url = f"{NITTER_BASE_URL}/{username}/rss"
    logging.info(f"nitter_bot: Prüfe Feed {feed_url}")
    feed = feedparser.parse(feed_url)
    if getattr(feed, "bozo", False):
        logging.warning(f"nitter_bot: feedparser bozo für {feed_url}: {getattr(feed, 'bozo_exception', None)}")
    return list(feed.entries or [])


def collect_for_user(
    username: str,
    history_map: dict[str, list[str]],
    seen_ids: set[str],
    per_user_limits: dict[str, int] | None,
    now_ts: float,
    max_age_seconds: int | None,
) -> tuple[list[dict], bool]:
    new_items: list[dict] = []
    history_changed = False
    try:
        entries = fetch_feed(username)
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Abrufen des Feeds für {username}: {exc}")
        return new_items, history_changed

    feed_len = len(entries or [])
    if per_user_limits is not None:
        per_user_limits[username] = feed_len if feed_len else HISTORY_LIMIT

    user_history = history_map.setdefault(username, [])

    for entry in entries:
        try:
            basics = get_basic_entry(entry, username)
        except Exception as exc:
            logging.error(f"nitter_bot: Fehler beim Auslesen der Metadaten für {username}: {exc}")
            continue

        status_id = basics.get("status_id", "")
        if not status_id or status_id in seen_ids:
            continue

        published_ts = basics.get("published_ts", now_ts) or now_ts
        is_too_old = False
        if max_age_seconds:
            try:
                is_too_old = (now_ts - float(published_ts)) > max_age_seconds
            except Exception:
                is_too_old = False

        if is_too_old:
            seen_ids.add(status_id)
            history_entry = (
                basics.get("canonical_url")
                or basics.get("source_url")
                or basics.get("link_local_fixed")
                or ""
            )
            if history_entry:
                user_history.append(history_entry)
                history_changed = True
            age_minutes = (now_ts - float(published_ts)) / 60
            logging.info(
                f"nitter_bot: Überspringe alten Eintrag {status_id} von {username} "
                f"({age_minutes:.1f} Minuten)."
            )
            continue

        try:
            parsed = parse_entry(entry, username, basics)
        except Exception as exc:
            logging.error(f"nitter_bot: Fehler beim Parsen eines Eintrags für {username}: {exc}")
            continue

        seen_ids.add(status_id)
        history_entry = parsed.get("canonical_url") or parsed.get("link", "")
        if history_entry:
            user_history.append(history_entry)
            history_changed = True
        new_items.append(parsed)

    return new_items, history_changed


def build_tweet_payloads(items: list[dict]):
    if not items:
        return []

    items.sort(key=lambda item: item.get("published_ts", time.time()))
    new_tweets = []
    for item in items:
        images = item.get("images") or []
        videos = item.get("videos") or []
        extern_urls = item.get("extern_urls") or []
        extern_urls_as_string = "\n".join(extern_urls)

        new_tweets.append(
            {
                "user": item["username"],
                "username": item["username"],
                "content": item.get("content", ""),
                "posted_time": item.get("posted_time", ""),
                "var_href": item.get("canonical_url") or item.get("link", ""),
                "status_id": item.get("status_id", ""),
                "images": images,
                "videos": videos,
                "extern_urls": extern_urls,
                "images_as_string": "\n".join(images),
                "videos_as_string": "\n".join(videos),
                "extern_urls_as_string": extern_urls_as_string,
                "quote_refs": item.get("quote_refs") or [],
            }
        )
    return new_tweets


async def main():
    parser = argparse.ArgumentParser(description="Poll lokale Nitter-RSS-Feeds.")
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--debug",
        action="store_true",
        help="Keine Auslieferung, keine DB-Updates; Ausgabe neuer Items auf STDOUT.",
    )
    mode_group.add_argument(
        "--nosending",
        "--no-send",
        "--dry-run",
        dest="no_send",
        action="store_true",
        help="Keine Auslieferung, aber DB/History wird aktualisiert.",
    )
    args = parser.parse_args()
    persist_history = not args.debug
    age_limit_seconds = None if args.debug else (MAX_ITEM_AGE_SECONDS or None)

    user_configs = build_user_configs(persist=persist_history)
    if not user_configs:
        logging.error("nitter_bot: Keine Benutzerkonfiguration gefunden.")
        return

    history_map, seen_ids, history_needs_save = load_history(persist=persist_history)
    if not persist_history and history_needs_save:
        logging.info("nitter_bot: History wurde bereinigt (Debug-Modus), Änderungen nicht gespeichert.")
        history_needs_save = False
    per_user_limits: dict[str, int] = {}

    for cfg in user_configs:
        start = cfg.get("active_start")
        end = cfg.get("active_end")
        if start and end:
            logging.info(
                f"nitter_bot: {cfg['username']} alle {cfg['interval']}s, Fenster {start}-{end}"
            )
        else:
            logging.info(f"nitter_bot: {cfg['username']} alle {cfg['interval']}s (durchgehend)")

    while True:
        try:
            now = datetime.now(tz=BERLIN_TZ)
            now_ts = now.timestamp()
            earliest_next = now_ts + max(POLL_INTERVAL, 30)
            new_items: list[dict] = []
            history_changed = history_needs_save
            history_needs_save = False

            for cfg in user_configs:
                within_window, next_start_ts = _is_within_window(now, cfg.get("active_start"), cfg.get("active_end"))
                if not within_window:
                    if next_start_ts:
                        cfg["next_run"] = max(cfg.get("next_run", 0.0), next_start_ts)
                        earliest_next = min(earliest_next, cfg["next_run"])
                    continue

                if cfg.get("next_run", 0.0) == 0.0:
                    cfg["next_run"] = now_ts

                if now_ts >= cfg["next_run"]:
                    fetched, user_history_changed = collect_for_user(
                        cfg["username"],
                        history_map,
                        seen_ids,
                        per_user_limits,
                        now_ts,
                        age_limit_seconds,
                    )
                    if fetched:
                        new_items.extend(fetched)
                    if user_history_changed:
                        history_changed = True
                    cfg["next_run"] = now_ts + cfg["interval"]

                earliest_next = min(earliest_next, cfg["next_run"])

            if history_changed and persist_history:
                history_map = save_history(history_map, per_user_limits)

            if new_items:
                new_tweets = build_tweet_payloads(new_items)
                if new_tweets:
                    if args.debug:
                        for t in new_tweets:
                            print(json.dumps(t, ensure_ascii=False, indent=2))
                    elif args.no_send:
                        print(f"No-send: {len(new_tweets)} neue Items (DB aktualisiert, keine Auslieferung).")
                        for t in new_tweets:
                            print(json.dumps(t, ensure_ascii=False, indent=2))
                    else:
                        try:
                            await telegram_bot.main(new_tweets)
                        except Exception as exc:
                            logging.error(f"nitter_bot: Fehler in telegram_bot: {exc}")

                        try:
                            await mastodon_bot.main(new_tweets)
                        except Exception as exc:
                            logging.error(f"nitter_bot: Fehler in mastodon_bot: {exc}")

        except Exception as exc:
            logging.error(f"nitter_bot: Unerwarteter Fehler: {exc}")

        sleep_for = max(15, min(120, earliest_next - time.time()))
        await asyncio.sleep(sleep_for)


if __name__ == "__main__":
    asyncio.run(main())
