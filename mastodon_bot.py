import asyncio
import os
import logging
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from typing import Any
from PIL import Image
import io
import cairosvg
import json

import aiohttp
import aiofiles

from mastodon import Mastodon
from google import genai
from google.genai import types  # <- für system_instruction
from gemini_helper import GeminiModelManager

# Initialize the Google Gemini client with the API key
client = genai.Client(api_key=os.environ.get("GEMINI_API_KEY"))
gemini_manager = GeminiModelManager(client)

# Configure logging
LOG_PATH = '/home/sascha/bots/twitter_bot.log'
ALT_TEXT_LOG_PREFIX = "Alt-Text Generierung"
GEMINI_HELPER_PREFIX = "gemini_helper"
ALT_TEXT_FALLBACK = "Alt-Text konnte nicht automatisch generiert werden."

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s:%(message)s'
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
RULES_FILE = "/home/sascha/bots/mastodon_rules.json"
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


def _empty_rules() -> dict:
    return {"users": {}}


def load_tagging_rules() -> dict:
    if not os.path.exists(RULES_FILE):
        return _empty_rules()
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return _empty_rules()
            data.setdefault("users", {})
            return data
    except Exception as e:
        logging.error(f"mastodon_bot: Fehler beim Laden der Tagging-Regeln: {e}")
        return _empty_rules()


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


def sanitize_for_mastodon(text: str) -> str:
    text = text.replace('@', '#')
    while '##' in text:
        text = text.replace('##', '#')
    text = text.replace('https://x.com', 'x')
    return text


def truncate_with_ellipsis(text: str, max_len: int) -> str:
    if max_len <= 0:
        return ""
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return "…"
    return text[:max_len - 1] + "…"


def build_mastodon_message(
    username: str,
    tweet_text: str,
    var_href: str | None,
    extern_urls: str | None,
    posted_time: str | None,
    max_len: int = MASTODON_MAX_CHARS
) -> str:
    """
    Tweet-Text hat Priorität.
    Wenn zu lang: entferne in dieser Reihenfolge:
      1) footer
      2) src_line
      3) posted_time
      4) extern_urls
    Erst wenn Header+Tweet selbst zu lang: Tweet kürzen (ohne Footer/Meta).
    """
    tweet_text = tweet_text or ""
    header = f"#{username}:\n\n"
    footer = "\n\n#öpnv_berlin_bot"

    src_line  = f"\n\nsrc: {var_href}" if var_href else ""
    urls_line = f"\n{extern_urls}" if extern_urls else ""
    time_line = f"\n{posted_time}" if posted_time else ""

    # Header + Tweet zu lang => Tweet kürzen (ohne Footer/Meta)
    if len(header) + len(tweet_text) > max_len:
        available = max_len - len(header)
        msg = header + truncate_with_ellipsis(tweet_text, available)
        return sanitize_for_mastodon(msg)[:max_len]

    include_footer = True
    include_src = bool(src_line)
    include_time = bool(time_line)
    include_urls = bool(urls_line)

    def assemble() -> str:
        msg = header + tweet_text + (footer if include_footer else "")
        meta = ""
        if include_src:
            meta += src_line
        if include_urls:
            meta += urls_line
        if include_time:
            meta += time_line
        return msg + meta

    # Alles drin
    msg = assemble()
    if len(msg) <= max_len:
        return sanitize_for_mastodon(msg)[:max_len]

    # Entfernen: footer -> src -> time -> urls
    if include_footer:
        include_footer = False
        msg = assemble()
        if len(msg) <= max_len:
            return sanitize_for_mastodon(msg)[:max_len]

    if include_src:
        include_src = False
        msg = assemble()
        if len(msg) <= max_len:
            return sanitize_for_mastodon(msg)[:max_len]

    if include_time:
        include_time = False
        msg = assemble()
        if len(msg) <= max_len:
            return sanitize_for_mastodon(msg)[:max_len]

    if include_urls:
        include_urls = False
        msg = assemble()
        if len(msg) <= max_len:
            return sanitize_for_mastodon(msg)[:max_len]

    # Fallback: Tweet kürzen
    available = max_len - len(header)
    msg = header + truncate_with_ellipsis(tweet_text, available)
    return sanitize_for_mastodon(msg)[:max_len]


async def post_tweet(mastodon, message, username, instance_name, in_reply_to_id=None):
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
            mastodon.status_post,
            message,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id
        )
    except Exception as e:
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


def prepare_image_for_upload(orig_image_bytes, ext):
    if ext == '.svg':
        orig_image_bytes = cairosvg.svg2png(bytestring=orig_image_bytes)
    elif ext not in ['.jpg', '.jpeg', '.png']:
        with Image.open(io.BytesIO(orig_image_bytes)) as img:
            output_io = io.BytesIO()
            img.convert('RGB').save(output_io, format='JPEG')
            orig_image_bytes = output_io.getvalue()
    return process_image_for_mastodon(orig_image_bytes)


async def prepare_media_payloads(images, original_tweet_full: str, twitter_account: str, tweet_url: str):
    """
    Holt Bilder (lokal oder remote), konvertiert sie und generiert je Bild einmal Alt-Text.
    Gibt Liste von Payloads mit Bytes + Alt-Text zurück.
    """
    payloads = []
    images = images[:4]  # Mastodon: max 4 Bilder
    async with aiohttp.ClientSession() as session:
        for image_link in images:
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

    return payloads


async def prepare_video_payload(video_url: str, tweet_text: str, twitter_account: str, tweet_url: str):
    """
    Lädt ein Video und erstellt einen Alt-Text auf Basis des Tweet-Texts.
    Falls Download fehlschlägt, return None.
    """
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


async def post_tweet_with_media(mastodon, message, media_payloads, instance_name, username: str, in_reply_to_id=None):
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

        status = await asyncio.to_thread(
            mastodon.status_post,
            message,
            media_ids=media_ids,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id
        )
        return status

    except Exception as e:
        logging.error(f"mastodon_bot: Allgemeiner Fehler beim Posten mit Bildern: {e}")
        return None


async def post_tweet_with_images_legacy(mastodon, message, media_payloads, instance_name, username: str, in_reply_to_id=None):
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

        status = await asyncio.to_thread(
            mastodon.status_post,
            message,
            media_ids=media_ids,
            visibility=visibility,
            in_reply_to_id=in_reply_to_id
        )
        return status
    except Exception as e:
        logging.error(f"mastodon_bot: Fallback-Fehler beim Posten mit Bildern: {e}")
        return None


async def main(new_tweets, thread: bool = False):
    print("Entering main function")

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

    reply_context = {name: None for name, _ in clients} if thread else None

    for tweet in new_tweets:
        username = tweet.get('username', '') or ""
        content_raw = tweet.get('content', '') or ""  # Original-Tweet unverändert

        posted_time = tweet.get('posted_time', '') or ""
        var_href = tweet.get('var_href', '') or ""     # Tweet-URL (Quelle)
        extern_urls = tweet.get('extern_urls', '') or ""
        images = tweet.get('images', []) or []
        videos = tweet.get('videos', []) or []
        image_payloads = None

        if isinstance(extern_urls, list):
            extern_urls = "\n".join([str(x) for x in extern_urls if x])

        _hashtags = extract_hashtags(content_raw, username)

        message = build_mastodon_message(
            username=username,
            tweet_text=content_raw,
            var_href=var_href,
            extern_urls=extern_urls,
            posted_time=posted_time,
            max_len=MASTODON_MAX_CHARS
        )

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
            reply_to_id = reply_context.get(instance_name) if reply_context is not None else None
            status_obj = None
            if media_payloads:
                print(f"Posting tweet with media for {username} to {instance_name}")
                status_obj = await post_tweet_with_media(
                    mastodon=mastodon,
                    message=message,
                    media_payloads=media_payloads,
                    instance_name=instance_name,
                    username=username,
                    in_reply_to_id=reply_to_id,
                )
                # Fallback: wenn Video scheitert, versuche Bilder (sofern vorhanden)
                if status_obj is None and use_video and images:
                    if image_payloads is None:
                        image_payloads = await prepare_media_payloads(
                            images=images,
                            original_tweet_full=content_raw,
                            twitter_account=username,
                            tweet_url=var_href
                        )
                    if image_payloads:
                        status_obj = await post_tweet_with_media(
                            mastodon=mastodon,
                            message=message,
                            media_payloads=image_payloads,
                            instance_name=instance_name,
                            username=username,
                            in_reply_to_id=reply_to_id,
                        )
                # Fallback: wenn nur Bilder und generischer Upload scheitert, nutze Legacy-Bild-Upload
                if status_obj is None and not use_video and image_payloads:
                    status_obj = await post_tweet_with_images_legacy(
                        mastodon=mastodon,
                        message=message,
                        media_payloads=image_payloads,
                        instance_name=instance_name,
                        username=username,
                        in_reply_to_id=reply_to_id,
                    )
            else:
                print(f"Posting tweet without images for {username} to {instance_name}")
                status_obj = await post_tweet(
                    mastodon,
                    message,
                    username,
                    instance_name,
                    in_reply_to_id=reply_to_id
                )

            if status_obj:
                asyncio.create_task(notify_control_bot(instance_name, status_obj, content_raw))
                if reply_context is not None:
                    status_id = status_obj.get("id") if hasattr(status_obj, "get") else getattr(status_obj, "id", None)
                    if status_id:
                        reply_context[instance_name] = status_id

    print("Main function completed")


print("Script loaded")
