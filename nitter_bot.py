#!/usr/bin/env python3

import argparse
import asyncio
import csv
import html
import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, time as dtime
from urllib.parse import urlparse

import feedparser
from dateutil.parser import parse
import pytz
import requests

import telegram_bot
import mastodon_bot

BASE_DIR = os.environ.get("BOTS_BASE_DIR", "/home/sascha/bots")
LOG_PATH = os.path.join(BASE_DIR, "twitter_bot.log")
LIST_FILE = os.path.join(BASE_DIR, "List_of_Twitter_users.txt")
ALT_LIST_FILE = os.path.join(os.path.dirname(__file__), "List_of_Twitter_users.txt")
HISTORY_FILE = os.path.join(BASE_DIR, "nitter_existing_tweets.txt")
USERS_CSV = os.path.join(BASE_DIR, "nitter_users.csv")
ALT_USERS_CSV = os.path.join(os.path.dirname(__file__), "nitter_users.csv")
POLL_INTERVAL = int(os.environ.get("NITTER_POLL_INTERVAL", "60"))
HISTORY_LIMIT = int(os.environ.get("NITTER_HISTORY_LIMIT", "200"))

DEFAULT_INTERVAL = 15 * 60  # Sekunden
USER_OVERRIDES = {
    "sbahnberlin": {
        "interval": 2 * 60,
        "active_start": "05:55",
        "active_end": "22:05",
    }
}

BERLIN_TZ = pytz.timezone("Europe/Berlin")

NITTER_BASE_URL = os.environ.get("NITTER_BASE_URL", "http://localhost:8081").rstrip("/")

_base_netloc = urlparse(NITTER_BASE_URL).netloc or urlparse(NITTER_BASE_URL).path
_base_host = _base_netloc.split(":")[0]
_base_scheme = urlparse(NITTER_BASE_URL).scheme or "http"
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


def _resolve_list_file() -> str:
    if os.path.exists(LIST_FILE):
        return LIST_FILE
    if os.path.exists(ALT_LIST_FILE):
        return ALT_LIST_FILE
    return LIST_FILE


def load_usernames():
    path = _resolve_list_file()
    if not os.path.exists(path):
        logging.error(f"nitter_bot: List_of_Twitter_users.txt nicht gefunden unter {path}")
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            names = [line.strip() for line in f if line.strip()]
        return names
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Lesen von {path}: {exc}")
        return []


def parse_time(value: str | None) -> dtime | None:
    if not value:
        return None
    try:
        hh, mm = value.split(":")
        return dtime(int(hh), int(mm))
    except Exception:
        logging.error(f"nitter_bot: Ungueltige Zeitangabe '{value}', erwartet HH:MM")
        return None


def ensure_users_csv() -> str:
    if os.path.exists(USERS_CSV):
        return USERS_CSV
    if os.path.exists(ALT_USERS_CSV):
        return ALT_USERS_CSV

    names = load_usernames()
    if not names:
        logging.error("nitter_bot: Keine Nutzerliste gefunden, CSV kann nicht erstellt werden.")
        return USERS_CSV

    os.makedirs(os.path.dirname(USERS_CSV) or ".", exist_ok=True)
    try:
        with open(USERS_CSV, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f, fieldnames=["username", "interval_seconds", "active_start", "active_end"]
            )
            writer.writeheader()
            for name in names:
                override = USER_OVERRIDES.get(name.lower(), {})
                writer.writerow(
                    {
                        "username": name,
                        "interval_seconds": override.get("interval", DEFAULT_INTERVAL),
                        "active_start": override.get("active_start", ""),
                        "active_end": override.get("active_end", ""),
                    }
                )
        logging.info(f"nitter_bot: {USERS_CSV} aus List_of_Twitter_users.txt erstellt.")
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Schreiben von {USERS_CSV}: {exc}")

    return USERS_CSV


def build_user_configs():
    csv_path = ensure_users_csv()
    configs = []

    if not os.path.exists(csv_path):
        logging.error(f"nitter_bot: CSV mit Nutzerintervallen fehlt: {csv_path}")
        return configs

    try:
        with open(csv_path, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                username = (row.get("username") or "").strip()
                if not username:
                    continue

                try:
                    interval = int(row.get("interval_seconds") or DEFAULT_INTERVAL)
                except Exception:
                    interval = DEFAULT_INTERVAL
                if interval <= 0:
                    interval = DEFAULT_INTERVAL

                active_start = parse_time((row.get("active_start") or "").strip() or None)
                active_end = parse_time((row.get("active_end") or "").strip() or None)

                configs.append(
                    {
                        "username": username,
                        "interval": interval,
                        "active_start": active_start,
                        "active_end": active_end,
                        "next_run": 0.0,
                    }
                )
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Lesen von {csv_path}: {exc}")

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


def normalize_url(url: str) -> str:
    cleaned = (url or "").strip()
    cleaned = cleaned.strip(".,;:!?()[]{}<>\"'…")
    cleaned = cleaned.replace("%E2%80%A6", "")
    if not cleaned:
        return ""
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", cleaned):
        cleaned = cleaned.lstrip("/")
        cleaned = f"https://{cleaned}"
    if cleaned.startswith("http://"):
        cleaned = "https://" + cleaned[len("http://") :]
    return cleaned


def expand_short_urls(urls: list[str]) -> list[str]:
    expanded_urls = []
    seen: set[str] = set()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; nitter-bot/1.0)"}
    for raw in urls:
        url = normalize_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            response = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
            status_ok = response is not None and 200 <= response.status_code < 400

            if (not status_ok) and ("dlvr.it" in url.lower()):
                response = requests.get(url, allow_redirects=True, timeout=8, headers=headers)
                status_ok = response is not None and 200 <= response.status_code < 400

            if status_ok:
                expanded_urls.append(response.url)
            else:
                status_code = getattr(response, "status_code", "unknown")
                log_fn = logging.warning
                if isinstance(status_code, int) and status_code >= 500:
                    log_fn = logging.error
                log_fn(f"nitter_bot: Überprüfung URL {url} liefert ungültigen Status {status_code}")
        except Exception as ex:
            logging.error(f"nitter_bot: Fehler beim Überprüfen der URL {url}: {ex}")

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


def load_history() -> tuple[dict[str, list[str]], set[str]]:
    """
    Lädt History als Mapping pro Nutzer. Unterstützt Legacy-Format (Plaintext-Zeilen).
    """
    history_map: dict[str, list[str]] = {}
    seen_ids: set[str] = set()
    if not os.path.exists(HISTORY_FILE):
        return history_map, seen_ids

    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            content = f.read()

        if not content or not content.strip():
            return history_map, seen_ids

        content_stripped = content.lstrip()
        if content_stripped.startswith("{"):
            try:
                data = json.loads(content)
            except Exception as exc:
                logging.error(f"nitter_bot: Ungültige JSON-History: {exc}")
                return history_map, seen_ids

            if isinstance(data, dict):
                for user, entries in data.items():
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
                        status_id = extract_status_id_from_url(entry)
                        if status_id:
                            seen_ids.add(status_id)
                    if normalized_entries:
                        history_map[str(user)] = normalized_entries
        else:
            legacy_entries = []
            for line in content.splitlines():
                entry = line.strip()
                if not entry:
                    continue
                legacy_entries.append(entry)
                status_id = extract_status_id_from_url(entry)
                if status_id:
                    seen_ids.add(status_id)
            if legacy_entries:
                history_map["_legacy"] = legacy_entries
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Lesen der History: {exc}")

    return history_map, seen_ids


def save_history(history_map: dict[str, list[str]], per_user_limits: dict[str, int] | None = None):
    """
    Speichert History pro Nutzer als JSON, pro User auf Limit begrenzt.
    """
    trimmed_map: dict[str, list[str]] = {}
    for user, entries in history_map.items():
        limit = HISTORY_LIMIT
        if per_user_limits:
            try:
                limit = max(1, int(per_user_limits.get(user, HISTORY_LIMIT)))
            except Exception:
                limit = HISTORY_LIMIT
        trimmed_map[user] = (entries or [])[-limit:]

    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(trimmed_map, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Schreiben der History: {exc}")


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


def parse_entry(entry, feed_username: str):
    status_id_raw = str(entry.get("id") or entry.get("guid") or "").strip()
    status_id = sanitize_status_id(status_id_raw)
    if not status_id:
        status_id = extract_status_id_from_url(entry.get("link", ""))

    username = (entry.get("author") or "").lstrip("@").strip() or feed_username

    summary_html = entry.get("summary") or entry.get("description") or ""
    title_text = html.unescape(entry.get("title", "") or "")

    content_text, images, videos, extern_urls = parse_summary(summary_html)
    if not content_text:
        content_text = html_to_text(title_text)

    extern_urls.extend(extract_urls_from_text(title_text))
    extern_urls = dedupe_preserve_order(extern_urls)
    extern_urls = expand_short_urls(extern_urls)
    extern_urls = dedupe_preserve_order([normalize_url(u) for u in extern_urls])
    if extern_urls:
        content_text = strip_urls_from_text(content_text, extern_urls)
    content_text = remove_truncated_url_tokens(content_text)
    content_text = replace_mentions_with_hash(content_text)

    published_ts, posted_time = parse_published(entry)
    link_raw = entry.get("link", "") or ""
    link_local_fixed = add_port_if_local(link_raw)
    canonical_url = build_canonical_url(username, status_id, "")
    source_url = to_external_source_url(link_local_fixed, username, status_id)

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
):
    new_items = []
    try:
        entries = fetch_feed(username)
    except Exception as exc:
        logging.error(f"nitter_bot: Fehler beim Abrufen des Feeds für {username}: {exc}")
        return new_items

    feed_len = len(entries or [])
    if per_user_limits is not None:
        per_user_limits[username] = feed_len if feed_len else HISTORY_LIMIT

    user_history = history_map.setdefault(username, [])

    for entry in entries:
        try:
            parsed = parse_entry(entry, username)
        except Exception as exc:
            logging.error(f"nitter_bot: Fehler beim Parsen eines Eintrags für {username}: {exc}")
            continue

        status_id = parsed.get("status_id", "")
        if not status_id or status_id in seen_ids:
            continue

        seen_ids.add(status_id)
        history_entry = parsed.get("canonical_url") or parsed.get("link", "")
        if history_entry:
            user_history.append(history_entry)
        new_items.append(parsed)

    return new_items


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
                "images": images,
                "videos": videos,
                "extern_urls": extern_urls,
                "images_as_string": "\n".join(images),
                "videos_as_string": "\n".join(videos),
                "extern_urls_as_string": extern_urls_as_string,
            }
        )
    return new_tweets


async def main():
    parser = argparse.ArgumentParser(description="Poll lokale Nitter-RSS-Feeds.")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Keine Auslieferung, sondern Ausgabe der neuen Items auf STDOUT.",
    )
    args = parser.parse_args()

    user_configs = build_user_configs()
    if not user_configs:
        logging.error("nitter_bot: Keine Benutzer in List_of_Twitter_users.txt gefunden.")
        return

    history_map, seen_ids = load_history()
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
                    fetched = collect_for_user(cfg["username"], history_map, seen_ids, per_user_limits)
                    if fetched:
                        new_items.extend(fetched)
                    cfg["next_run"] = now_ts + cfg["interval"]

                earliest_next = min(earliest_next, cfg["next_run"])

            if new_items:
                save_history(history_map, per_user_limits)
                new_tweets = build_tweet_payloads(new_items)
                if new_tweets:
                    if args.debug:
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
