import asyncio
import html
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from mastodon import Mastodon
from mastodon_text_utils import split_mastodon_text

# ----------------------------
# Konstanten / Pfade
# ----------------------------
# Serverseitiges Rate-Limit vermeiden: Minimum 5s, Default 90s (Empfehlung)
POLL_INTERVAL_SEC = max(5, int(os.environ.get("MASTODON_CONTROL_POLL_INTERVAL", "90")))

# Gleiche Instanzen wie mastodon_bot
INSTANCES = {
    "opnv_berlin":   {"access_token_env": "opnv_berlin",   "api_base_url": "https://berlin.social"},
    "opnv_toot":     {"access_token_env": "opnv_toot",     "api_base_url": "https://toot.berlin"},
    "opnv_mastodon": {"access_token_env": "opnv_mastodon", "api_base_url": "https://mastodon.berlin"},
}

# Logfile (nur zentrales Log)
BOT_LOG_FILE = "/home/sascha/bots/twitter_bot.log"

# Pfad für Tagging-Regeln
RULES_FILE = "/home/sascha/bots/mastodon_rules.json"

# Bots für Fehlerzählung (letzte 24h) im /status
ALT_TEXT_CATEGORY = "Alt-Text Generierung"
GEMINI_HELPER_CATEGORY = "gemini_helper"

BOT_NAMES_FOR_COUNT = [
    "twitter_bot",
    "telegram_bot",
    "mastodon_bot",
    "bsky_bot",
    "nitter_bot",
    ALT_TEXT_CATEGORY,
    GEMINI_HELPER_CATEGORY,
]

# Marker für typische DBus/Systemd-Fehler (erlaubt robusten Fallback)
BUS_ERROR_MARKERS = (
    "Failed to connect to bus",
    "Kein Medium gefunden",
    "System has not been booted with systemd",
    "Failed to connect to system bus",
)

# Zeitstempel am Anfang der Zeile
TS_RX = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(?P<rest>.*)$")

# Mastodon DM Limit (inkl. Prefix mit Mention etwas Luft lassen)
MASTODON_DM_MAX = 450

# Zeitzone Berlin
BERLIN_TZ = ZoneInfo("Europe/Berlin")

# Event-Bridge vom mastodon_bot
EVENT_HOST = os.environ.get("MASTODON_CONTROL_EVENT_HOST", "127.0.0.1")
EVENT_PORT = int(os.environ.get("MASTODON_CONTROL_EVENT_PORT", "8123"))
EVENT_ENABLED = os.environ.get("MASTODON_CONTROL_EVENT_ENABLED", "1").lower() not in {"0", "false", "no"}

# Instanz-Registry für Events (wird in _run_instance befüllt)
INSTANCE_CLIENTS: dict[str, Mastodon] = {}

# Account-Gruppen (für Regel-Target-Auswahl)
ACCOUNT_GROUPS = {
    "sbahn": ["SBahnBerlin"],
    "viz": ["VIZ_Berlin"],
    "db": ["DBRegio_BB"],
    "polizei": ["polizeiberlin", "bpol", "Berliner_FW"],
    "vbbvip": ["vbb", "VIP"],
}


def setup_logging():
    fmt = logging.Formatter("%(asctime)s %(levelname)s:%(message)s")
    logger = logging.getLogger()
    logger.setLevel(logging.WARNING)
    if logger.handlers:
        logger.handlers.clear()

    try:
        handler = logging.FileHandler(BOT_LOG_FILE)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(fmt)
        logger.addHandler(handler)
    except Exception:
        # Falls das zentrale Log nicht schreibbar ist, nicht crashen
        pass


setup_logging()

# Dialog-State (im RAM, reicht für einfache DM-Flows)
USER_STATES: dict[str, dict] = {}

YES = {"ja", "j", "yes", "y"}
NO = {"nein", "n", "no"}


# ----------------------------
# Helpers: Command Exec (Userrechte, ohne sudo)
# ----------------------------
def _run_cmd_no_sudo(cmd: list[str], timeout: int = 3) -> tuple[int, str]:
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = (res.stdout or "").strip()
        err = (res.stderr or "").strip()
        return res.returncode, (out if out else err)
    except FileNotFoundError:
        return 127, "command-not-found"
    except Exception as e:
        return 1, f"error:{e}"


def _looks_like_bus_error(output: str) -> bool:
    o = output or ""
    return any(m in o for m in BUS_ERROR_MARKERS)


def _pgrep_any(patterns: list[str]) -> tuple[bool, str]:
    my_pid = str(os.getpid())

    for pat in patterns:
        rc, out = _run_cmd_no_sudo(["pgrep", "-fa", pat], timeout=2)
        if rc == 0 and out:
            for line in out.splitlines():
                if my_pid in line:
                    continue
                if "mastodon_control_bot" in line:
                    continue
                return True, line.strip()

    return False, ""


def get_service_state(service_name: str, fallback_patterns: list[str]) -> tuple[str, str]:
    """
    Prüft sowohl --user als auch systemweit und nimmt den besten verfügbaren Status.
    Priorität: active > failed > inactive > unbekannt. Bei DBus-Problemen: pgrep-Fallback.
    """
    candidates = [
        ["systemctl", "--user", "is-active", service_name],
        ["systemctl", "is-active", service_name],
    ]

    bus_error = False
    best_state = None  # (prio, state, detail)
    state_priority = {"active": 0, "failed": 1, "inactive": 2, "unknown": 3}

    for cmd in candidates:
        rc, out = _run_cmd_no_sudo(cmd)
        status = (out or "").strip().lower() or "unknown"

        if _looks_like_bus_error(status):
            bus_error = True
            continue

        prio = state_priority.get(status, 3)
        detail = f"systemctl:{status}"
        if (best_state is None) or (prio < best_state[0]):
            best_state = (prio, status, detail)

        if status == "active":
            break

    if bus_error and (best_state is None or best_state[2].startswith("systemctl:unknown")):
        running, hit = _pgrep_any(fallback_patterns)
        if running:
            return "läuft", f"fallback:prozess ({hit})"
        return "gestoppt/abgestürzt", "fallback:kein prozess (systemd/dbus nicht erreichbar)"

    if best_state:
        status = best_state[1]
        detail = best_state[2]
        if status == "active":
            return "läuft", detail
        if status == "inactive":
            return "gestoppt", detail
        if status == "failed":
            return "abgestürzt", detail
        return "unbekannt", detail

    return "unbekannt", "keine daten"


def strip_html_content(content: str) -> str:
    text = content or ""
    text = text.replace("<br />", "\n").replace("<br>", "\n")
    text = re.sub(r"</p>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def clean_command_text(content: str) -> str:
    plain = strip_html_content(content)
    plain = re.sub(r"@\S+", "", plain)
    plain = re.sub(r"\s+", " ", plain)
    return plain.strip()


def parse_user_command(text: str) -> tuple[str | None, str]:
    """
    Erkennt Befehle am Satzanfang, auch ohne Slash. URLs werden vorher entfernt,
    damit https://... keine Fehl-Erkennung auslöst. Nur explizite Befehlswörter.
    """
    if not text:
        return None, ""

    # URLs entfernen
    no_urls = re.sub(r"https?://\S+", "", text)
    no_urls = re.sub(r"\w+://\S+", "", no_urls)

    stripped = no_urls.strip()
    if not stripped:
        return None, ""

    first, *rest = stripped.split(maxsplit=1)
    cmd_token = first.lstrip("/").lower().strip(".,;:!?")

    synonyms = {
        "status": ["status", "statusmeldung"],
        "help": ["help", "hilfe", "info", "anleitung"],
        "start": ["start", "starten"],
        "add": ["add", "neu", "neue"],
        "list": ["list", "liste", "zeigen", "anzeigen"],
        "overview": ["overview", "kurz", "kurzinfo", "kurzfassung", "summary", "uebersicht", "übersicht", "brief"],
        "delete": ["delete", "del", "loeschen", "löschen"],
        "pause": ["pause", "pausieren", "anhalten", "disable"],
        "resume": ["resume", "weiter", "enable", "fortsetzen"],
        "schedule": ["schedule", "zeit", "zeitplan"],
        "stop": ["stop", "stoppen", "aus"],
        "startover": ["startover", "reset", "zuruecksetzen", "zurücksetzen"],
        "about": ["about", "ueber", "über"],
        "datenschutz": ["datenschutz", "privacy", "data"],
    }

    for canonical, tokens in synonyms.items():
        if cmd_token in tokens:
            remainder = rest[0] if rest else ""
            return canonical, remainder

    return None, ""


def _is_yes(text: str) -> bool:
    return text.lower().strip() in YES


def _is_no(text: str) -> bool:
    return text.lower().strip() in NO


# ----------------------------
# Helpers: Keyword-Matching + Preview
# ----------------------------
def _match_keywords(content: str, keywords: list[str]) -> list[str]:
    if not keywords:
        return []
    lower = (content or "").lower()
    hits = []
    for kw in keywords:
        if kw and kw.lower() in lower:
            hits.append(kw)
    return hits


def _shorten(text: str, max_len: int = 240) -> str:
    t = (text or "").strip()
    return t if len(t) <= max_len else t[: max_len - 1] + "…"


# ----------------------------
# Helpers: Persistenz der Regeln
# ----------------------------
def _empty_rules() -> dict:
    return {"users": {}}


def load_rules() -> dict:
    if not os.path.exists(RULES_FILE):
        return _empty_rules()
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if not isinstance(data, dict):
                return _empty_rules()
            data.setdefault("users", {})
            return data
    except json.JSONDecodeError as e:
        logging.error(f"mastodon_control_bot: Fehler beim Laden der Regeln (inkonsistente Datei): {e}")
        _reset_rules_file("jsondecode")
        return _empty_rules()
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler beim Laden der Regeln: {e}")
        return _empty_rules()


def save_rules(data: dict):
    os.makedirs(os.path.dirname(RULES_FILE), exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=os.path.dirname(RULES_FILE),
            prefix=".tmp_mastorules_"
        ) as tmp:
            json.dump(data, tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp_path = tmp.name
        os.replace(tmp_path, RULES_FILE)
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler beim Speichern der Regeln: {e}")


def _reset_rules_file(reason: str):
    try:
        if os.path.exists(RULES_FILE):
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_path = f"{RULES_FILE}.{ts}.bak"
            shutil.copy2(RULES_FILE, backup_path)
    except Exception as e:
        logging.error(f"mastodon_control_bot: Backup der Regeln fehlgeschlagen ({reason}): {e}")
    try:
        save_rules(_empty_rules())
    except Exception as e:
        logging.error(f"mastodon_control_bot: Reset der Regeln fehlgeschlagen ({reason}): {e}")


def _get_status_id(status_obj):
    if status_obj is None:
        return None
    if hasattr(status_obj, "get"):
        try:
            return status_obj.get("id")
        except Exception:
            pass
    try:
        return getattr(status_obj, "id", None)
    except Exception:
        return None


def normalize_acct(acct: str) -> str:
    return (acct or "").lstrip("@").strip()


def ensure_user_config(acct: str, data: dict | None = None) -> tuple[dict, dict]:
    full = data if data is not None else load_rules()
    users = full.setdefault("users", {})
    key = normalize_acct(acct)
    if key not in users:
        users[key] = {
            "global_pause": True,
            "global_schedule": None,
            "rules": []
        }
    return users[key], full


def _next_rule_id() -> str:
    return f"r{int(datetime.now().timestamp() * 1000)}"


# ----------------------------
# Helpers: Feiertage + Zeit
# ----------------------------
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

    # Beweglich
    good_friday = easter - timedelta(days=2)
    easter_monday = easter + timedelta(days=1)
    ascension = easter + timedelta(days=39)
    whit_monday = easter + timedelta(days=50)

    holidays.update({good_friday, easter_monday, ascension, whit_monday})

    return d in holidays


def parse_time_window(window_str: str) -> tuple[str, str] | None:
    raw = (window_str or "").strip().lower()
    if not raw:
        return None

    # Erlaubt: 06:00-22:00, 6-22, 6 bis 22, 6-22h, 6:30–22:15
    m = re.match(r"^\s*(\d{1,2})(?::(\d{1,2}))?\s*(?:-|–|—|bis)\s*(\d{1,2})(?::(\d{1,2}))?\s*h?\s*$", raw)
    if not m:
        return None

    h1, m1, h2, m2 = m.groups()
    h1 = int(h1)
    h2 = int(h2)
    m1 = int(m1) if m1 is not None else 0
    m2 = int(m2) if m2 is not None else 0

    try:
        start = time(h1, m1)
        end = time(h2, m2)
    except Exception:
        return None

    if (h1, m1) == (h2, m2):
        return None

    return (start.strftime("%H:%M"), end.strftime("%H:%M"))


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
    # Über Mitternacht (z.B. 22:00-06:00)
    return now_t >= s or now_t <= e


def schedule_allows(schedule: dict | None, now: datetime) -> bool:
    if not schedule:
        return True

    windows = schedule.get("windows") or []
    days_mode = schedule.get("days") or "all"
    skip_holidays = bool(schedule.get("skip_holidays"))

    d = now.date()
    weekday = d.weekday()
    if isinstance(days_mode, list):
        if weekday not in days_mode:
            return False
    else:
        dm = str(days_mode).lower()
        if dm == "mon-fri":
            if weekday > 4:
                return False
        elif dm == "mon-sat":
            if weekday > 5:
                return False
        # "all" => immer aktiv

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


def describe_schedule(schedule: dict | None) -> str:
    if not schedule:
        return "immer aktiv"
    days = schedule.get("days") or "all"
    if isinstance(days, list):
        label_map = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
        days_label = ", ".join(label_map[d] for d in sorted(days) if 0 <= d <= 6)
    else:
        days_label = {
            "mon-fri": "Mo-Fr",
            "mon-sat": "Mo-Sa",
            "all": "alle Tage"
        }.get(str(days).lower(), str(days))
    windows = schedule.get("windows") or []
    window_txt = ", ".join([f"{w.get('start')} - {w.get('end')}" for w in windows]) if windows else "00:00 - 23:59"
    holiday_txt = " (Berliner Feiertage übersprungen)" if schedule.get("skip_holidays") else ""
    return f"{days_label}, {window_txt}{holiday_txt}"


def compose_schedule(time_window: tuple[str, str] | None, days_mode, skip_holidays: bool) -> dict | None:
    if time_window is None and (days_mode == "all" or days_mode is None) and not skip_holidays:
        return None
    windows = [{"start": "00:00", "end": "23:59"}] if time_window is None else [{"start": time_window[0], "end": time_window[1]}]
    return {
        "windows": windows,
        "days": days_mode,
        "skip_holidays": bool(skip_holidays),
    }


def parse_days_mode(val: str | None) -> str | None:
    if not val:
        return None
    v = val.lower().strip()
    if v in {"mo-fr", "mon-fri", "wochentag"}:
        return "mon-fri"
    if v in {"mo-sa", "mon-sat"}:
        return "mon-sat"
    if v in {"alle", "all", "every"}:
        return "all"
    single_map = {
        "mo": [0], "montag": [0], "monday": [0],
        "di": [1], "dienstag": [1], "tuesday": [1],
        "mi": [2], "mittwoch": [2], "wednesday": [2],
        "do": [3], "donnerstag": [3], "thursday": [3],
        "fr": [4], "freitag": [4], "friday": [4],
        "sa": [5], "samstag": [5], "saturday": [5],
        "so": [6], "sonntag": [6], "sunday": [6],
        "wochenende": [5, 6],
        "weekend": [5, 6],
    }
    if v in single_map:
        return single_map[v]
    return None


def parse_keywords(text: str) -> list[str]:
    raw = text or ""
    if "\n" in raw:
        parts = [line.strip() for line in raw.splitlines()]
    else:
        parts = [p.strip() for p in raw.split()]
    parts = [p for p in parts if p]
    seen = set()
    unique = []
    for k in parts:
        kl = k.lower()
        if kl in seen:
            continue
        seen.add(kl)
        unique.append(k)
    return unique


def parse_keywords_with_block(text: str) -> tuple[list[str], list[str]]:
    """
    Trennt Stichworte in erlaubte und blockierte.
    Blockierte werden mit führendem '--' oder '- -' markiert.
    """
    raw = text or ""
    if "\n" in raw:
        parts = [line.strip() for line in raw.splitlines()]
    else:
        parts = [p.strip() for p in raw.split()]

    positives: list[str] = []
    blocked: list[str] = []
    seen_pos: set[str] = set()
    seen_block: set[str] = set()

    for entry in parts:
        if not entry:
            continue
        token = entry.strip()
        is_blocked = False
        if token.startswith("- -"):
            is_blocked = True
            token = token[3:].strip()
        elif token.startswith("--"):
            is_blocked = True
            token = token[2:].strip()

        if not token:
            continue

        key = token.lower()
        if is_blocked:
            if key in seen_block:
                continue
            seen_block.add(key)
            blocked.append(token)
            continue

        if key in seen_block or key in seen_pos:
            continue
        seen_pos.add(key)
        positives.append(token)

    return positives, blocked


def _parse_date_token(token: str, today: date) -> date | None:
    t = (token or "").strip()
    if not t:
        return None
    low = t.lower()
    if low in {"heute"}:
        return today
    if low in {"morgen"}:
        return today + timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(t, fmt).date()
        except Exception:
            pass
    try:
        dt = datetime.strptime(t, "%d.%m").date().replace(year=today.year)
        if dt < today:
            dt = dt.replace(year=today.year + 1)
        return dt
    except Exception:
        return None


def parse_validity_window(text: str) -> tuple[date | None, date | None, str | None]:
    """
    Liefert (valid_from, valid_until, error_text).
    Unterstützt:
      - leer/immer/täglich (kein Limit)
      - bis <datum>
      - <datum1> - <datum2> oder <datum1> bis <datum2>
      - in X Tagen/Wochen
      - für X Tage/Wochen / X Tage lang / X Wochen lang
      - ab <datum>
      - einzelnes Datum (nur dieser Tag)
    """
    raw = (text or "").strip()
    if not raw:
        return None, None, None

    low = raw.lower()
    today = datetime.now(BERLIN_TZ).date()

    if low in {"immer", "dauerhaft", "täglich", "taeglich"}:
        return None, None, None

    bis_match = re.match(r"^\s*bis\s+(.+)$", raw, re.IGNORECASE)
    if bis_match:
        end_dt = _parse_date_token(bis_match.group(1), today)
        if not end_dt:
            return None, None, "Datum nach 'bis' nicht verstanden."
        return None, end_dt, None

    range_match = re.match(r"^\s*(.+?)\s*(?:-|–|—|bis)\s*(.+?)\s*$", raw, re.IGNORECASE)
    if range_match:
        start_raw, end_raw = range_match.groups()
        start_dt = _parse_date_token(start_raw, today)
        end_dt = _parse_date_token(end_raw, today)
        if not start_dt or not end_dt:
            return None, None, "Zeitraum nicht verstanden – bitte Datum angeben (z.B. 2025-02-01 bis 2025-02-10)."
        return start_dt, end_dt, None

    ab_match = re.match(r"^\s*ab\s+(.+)$", raw, re.IGNORECASE)
    if ab_match:
        start_dt = _parse_date_token(ab_match.group(1), today)
        if not start_dt:
            return None, None, "Datum nach 'ab' nicht verstanden."
        return start_dt, None, None

    in_match = re.match(r"^\s*in\s+(\d+)\s+(tag|tage|woche|wochen)\s*$", low, re.IGNORECASE)
    if in_match:
        count = int(in_match.group(1))
        unit = in_match.group(2)
        delta = count if unit.startswith("tag") else count * 7
        start_dt = today + timedelta(days=delta)
        return start_dt, None, None

    duration_match = re.match(r"^\s*(?:für|fuer)?\s*(\d+)\s+(tag|tage|woche|wochen)\s*(?:lang)?\s*$", low, re.IGNORECASE)
    if duration_match:
        count = int(duration_match.group(1))
        unit = duration_match.group(2)
        span_days = count if unit.startswith("tag") else count * 7
        start_dt = today
        end_dt = start_dt + timedelta(days=max(0, span_days - 1))
        return start_dt, end_dt, None

    single_dt = _parse_date_token(raw, today)
    if single_dt:
        return single_dt, single_dt, None

    return None, None, "Gültigkeit nicht verstanden. Beispiele: 'bis 2025-02-01', 'für 3 Tage', 'in 2 Wochen', '01.02.2025-10.02.2025'."


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


def describe_validity(valid_from: date | str | None, valid_until: date | str | None) -> str:
    start = _date_from_field(valid_from)
    end = _date_from_field(valid_until)
    if not start and not end:
        return "dauerhaft gültig"
    if start and end:
        return f"gültig von {start.isoformat()} bis {end.isoformat()}"
    if start:
        return f"gültig ab {start.isoformat()}"
    return f"gültig bis {end.isoformat()}"


def validity_allows(rule: dict, today: date) -> bool:
    start = _date_from_field(rule.get("valid_from"))
    end = _date_from_field(rule.get("valid_until"))
    if start and today < start:
        return False
    if end and today > end:
        return False
    return True


# ----------------------------
# Helpers: Status / Fehlerstatistik
# ----------------------------
def _reverse_read_lines(path: str, block_size: int = 8192):
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        buf = b""

        while pos > 0:
            read_size = min(block_size, pos)
            pos -= read_size
            f.seek(pos)
            chunk = f.read(read_size)
            buf = chunk + buf

            lines = buf.split(b"\n")
            buf = lines[0]

            for line in reversed(lines[1:]):
                yield line.decode("utf-8", errors="ignore")

        if buf:
            yield buf.decode("utf-8", errors="ignore")


def parse_ts_and_rest(line: str) -> tuple[datetime | None, str]:
    m = TS_RX.match(line.strip())
    if not m:
        return None, ""
    ts_str = m.group("ts")
    try:
        ts = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S,%f")
    except Exception:
        return None, ""
    return ts, (m.group("rest") or "")


def split_level_and_body(rest: str) -> tuple[str | None, str]:
    """
    Zerlegt 'ERROR:foo' oder 'WARNING:foo' in (Level, Body).
    Body enthält alles nach dem ersten ':'.
    """
    r = (rest or "").lstrip()
    if r.startswith("ERROR"):
        parts = r.split(":", 1)
        body = parts[1].lstrip() if len(parts) > 1 else ""
        return "ERROR", body
    if r.startswith("WARNING"):
        parts = r.split(":", 1)
        body = parts[1].lstrip() if len(parts) > 1 else ""
        return "WARNING", body
    return None, r


def detect_bot_and_message(rest: str, bot_names: list[str]) -> tuple[str, str]:
    r = rest or ""
    best_bot = None
    best_idx = None

    for bot in bot_names:
        needle = f"{bot}:"
        idx = r.find(needle)
        if idx != -1:
            if best_idx is None or idx < best_idx:
                best_idx = idx
                best_bot = bot

    if best_bot is None:
        return "nicht_zuordenbar", r.strip()

    msg = r[best_idx + len(best_bot) + 1:].strip()
    if not msg:
        msg = "(leer)"
    return best_bot, msg


def count_errors_since_grouped(
    log_path: str,
    bots: list[str],
    since_dt: datetime,
    levels: tuple[str, ...] = ("ERROR",)
) -> dict[str, dict[str, int]]:
    grouped: dict[str, dict[str, int]] = {b: {} for b in bots}
    level_set = {lvl.upper() for lvl in levels}

    if not os.path.exists(log_path):
        return grouped

    for line in _reverse_read_lines(log_path):
        line = line.strip()
        if not line:
            continue

        ts, rest = parse_ts_and_rest(line)
        if ts is None:
            continue

        if ts < since_dt:
            break

        level, body = split_level_and_body(rest)
        if level not in level_set:
            continue

        bot, msg = detect_bot_and_message(body, bots)
        if bot not in grouped:
            continue

        grouped[bot][msg] = grouped[bot].get(msg, 0) + 1

    return grouped


def build_status_text() -> str:
    """
    Gleicher Aufbau wie im telegram_control_bot: Status der Module + Fehleranzahl (24h).
    """
    try:
        twitter_state, _ = get_service_state(
            "twitter_bot.service",
            fallback_patterns=["twitter_bot", "twitter_bot.py", "/home/sascha/bots/twitter"]
        )
        bsky_state, _ = get_service_state(
            "bsky_bot.service",
            fallback_patterns=["bsky_bot", "bsky_bot.py", "/home/sascha/bots/bsky", "bluesky"]
        )
        nitter_state, _ = get_service_state(
            "nitter_bot.service",
            fallback_patterns=["nitter_bot", "nitter_bot.py", "/home/sascha/Dokumente/bots/nitter"]
        )

        twitter_running = twitter_state.startswith("läuft")
        nitter_running = nitter_state.startswith("läuft")

        # Für Nicht-Admins (alle Mastodon-Nutzer): nur den laufenden Bot zählen, wenn genau einer läuft,
        # sonst beide anzeigen.
        bot_groups = BOT_NAMES_FOR_COUNT
        show_twitter = True
        show_nitter = True
        if twitter_running != nitter_running:
            show_twitter = twitter_running
            show_nitter = nitter_running
        bot_groups = [
            b for b in BOT_NAMES_FOR_COUNT
            if not ((b == "twitter_bot" and not show_twitter) or (b == "nitter_bot" and not show_nitter))
        ]

        since = datetime.now() - timedelta(hours=24)
        grouped = count_errors_since_grouped(
            BOT_LOG_FILE,
            bot_groups,
            since,
            levels=("ERROR", "WARNING")
        )

        lines = []
        lines.append(f"Twitter-Modul: {twitter_state}")
        lines.append(f"Bluesky-Modul: {bsky_state}")
        lines.append(f"Nitter-Modul: {nitter_state}")
        lines.append("")
        lines.append("Fehler/Warnungen (letzte 24 Stunden):")
        lines.append("")

        for botname in bot_groups:
            bot_dict = grouped.get(botname, {}) or {}
            error_groups = len(bot_dict)
            occurrences = sum(bot_dict.values())
            lines.append(f"- {botname}")
            lines.append(f"  Fehlergruppen: {error_groups}")
            lines.append(f"  Auslösungen:   {occurrences}")
            lines.append("")

        return "\n".join(lines).strip()
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler in build_status_text: {e}")
        return "Status konnte nicht ermittelt werden."


# ----------------------------
# Helpers: DM-Senden
# ----------------------------
def send_dm(mastodon, acct: str, in_reply_to_id, text: str, include_tagging_hint: bool = True):
    prefix = f"@{normalize_acct(acct)} "
    base_max = max(1, MASTODON_DM_MAX - len(prefix))
    reply_to = in_reply_to_id
    tagging_hint = ""
    if include_tagging_hint:
        try:
            _, tagging_hint = tagging_mode_status(acct)
        except Exception:
            tagging_hint = ""

    # Suffix wird nur beim letzten Chunk angehängt; stelle sicher, dass auch dann das Limit eingehalten wird.
    suffix = f"\n\n(Aktuelle Antwortzeit: ca. {POLL_INTERVAL_SEC} Sekunden)"
    if tagging_hint:
        suffix += f"\n{tagging_hint}"

    # Falls das Suffix länger als der verfügbare Platz ist, kürzen (sollte praktisch nie vorkommen).
    if len(suffix) >= base_max:
        suffix = suffix[: max(0, base_max - 1)]

    final_max = max(1, base_max - len(suffix))
    safe_max = min(base_max, final_max)

    parts = split_mastodon_text(text, max_len=safe_max, sanitize=False)
    total = len(parts)

    for idx, part in enumerate(parts):
        part_suffix = suffix if idx == total - 1 else ""
        body = f"{prefix}{part}{part_suffix}"
        try:
            status = mastodon.status_post(
                body,
                visibility="direct",
                in_reply_to_id=reply_to
            )
            if status is not None:
                status_id = status.get("id") if hasattr(status, "get") else getattr(status, "id", None)
                if status_id:
                    reply_to = status_id
        except Exception as e:
            logging.error(f"mastodon_control_bot: Fehler beim Senden der DM: {e}")


# ----------------------------
# Tagging per Event-Bridge
# ----------------------------
async def _process_tag_event(instance_name: str, payload: dict):
    mastodon = INSTANCE_CLIENTS.get(instance_name)
    if mastodon is None:
        return

    rules = load_rules()
    users = rules.get("users") or {}
    if not users:
        return

    content = payload.get("content") or ""
    status_url = payload.get("url") or ""
    status_id = payload.get("status_id")

    now = datetime.now(BERLIN_TZ)
    today = now.date()
    for acct, cfg in users.items():
        if cfg.get("global_pause"):
            continue

        triggered_rules: list[dict] = []
        schedule_cache: dict[str, bool] = {}
        for rule in cfg.get("rules", []):
            if rule.get("paused"):
                continue
            insts = rule.get("instances") or ["alle"]
            if "alle" not in insts and instance_name not in insts:
                continue
            if not validity_allows(rule, today):
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
            if hits:
                triggered_rules.append({
                    "id": rule.get("id") or "?",
                    "hits": hits,
                })

        if not triggered_rules:
            continue

        trigger_lines = []
        for tr in triggered_rules:
            rid = tr.get("id") or "?"
            kws = ", ".join(tr.get("hits") or [])
            trigger_lines.append(f"- Regel {rid}: {kws}")

        msg = (
            f"Tagging-Modus: Treffer auf {instance_name}.\n\n"
            "Auslöser:\n"
            + "\n".join(trigger_lines)
            + "\n\n"
            + f"URL: {status_url or 'Keine URL bekannt'}\n\n"
            + "Tagging-Modus deaktivieren: /stop\n"
            + "Regel löschen: /delete <id> oder /del <id>\n"
            + "Regel pausieren: /pause <id>"
        ).strip()
        send_dm(mastodon, acct, status_id, msg)


async def _handle_event_connection(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    try:
        raw = await reader.read(65536)
        if not raw:
            return
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as e:
            logging.error(f"mastodon_control_bot: Ungültiges Event-Payload: {e}")
            return

        instance = payload.get("instance")
        if not instance:
            return

        asyncio.create_task(_process_tag_event(instance, payload))
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_event_listener():
    if not EVENT_ENABLED:
        return
    try:
        server = await asyncio.start_server(_handle_event_connection, host=EVENT_HOST, port=EVENT_PORT)
    except Exception as e:
        logging.error(f"mastodon_control_bot: Event-Listener konnte nicht gestartet werden: {e}")
        return

    async with server:
        try:
            await server.serve_forever()
        except asyncio.CancelledError:
            return


# ----------------------------
# UI Helpers
# ----------------------------
def format_rules_summary(acct: str) -> str:
    data = load_rules()
    users = data.get("users", {})
    user_cfg = users.get(normalize_acct(acct))
    state, state_text = tagging_mode_status(acct, data)

    lines = ["Deine Einstellungen", state_text]
    if not user_cfg:
        lines.append("Du hast noch keine Tagging-Regeln. Nutze /add, um eine anzulegen.")
        return "\n\n".join(lines)

    lines.append(f"Gesamtstatus: {'pausiert' if user_cfg.get('global_pause') else 'aktiv'}")
    gs = user_cfg.get("global_schedule")
    if gs:
        lines.append(f"Globales Zeitfenster: {describe_schedule(gs)}")
    else:
        lines.append("Kein globales Zeitfenster – es zählen die Zeiten in deinen Regeln.")

    rules = user_cfg.get("rules") or []
    if not rules:
        lines.append("Keine Regeln gespeichert. Lege mit /add los.")
    else:
        rule_blocks = []
        for r in rules:
            rid = r.get("id") or "?"
            status_txt = "pausiert" if r.get("paused") else "aktiv"
            sched_txt = describe_schedule(r.get("schedule"))
            targets = ", ".join(r.get("targets") or ["alle"])
            insts = ", ".join(r.get("instances") or ["alle"])
            kws = ", ".join(r.get("keywords") or [])
            blocked = ", ".join(r.get("blocked_keywords") or [])
            validity_txt = describe_validity(r.get("valid_from"), r.get("valid_until"))
            rule_blocks.append(
                f"Regel {rid} ({status_txt})\n"
                f"Wörter: {kws or 'keine'}\n"
                f"Blockierte Wörter: {blocked or 'keine'}\n"
                f"Ziele: {targets}\n"
                f"Instanzen: {insts}\n"
                f"Zeitfenster: {sched_txt}\n"
                f"Gültigkeit: {validity_txt}"
            )
        lines.append("Deine Regeln:\n\n" + "\n\n".join(rule_blocks))

    return "\n\n".join(lines)


def format_rules_overview(acct: str) -> str:
    data = load_rules()
    users = data.get("users", {})
    user_cfg = users.get(normalize_acct(acct))
    state, state_text = tagging_mode_status(acct, data)

    tag_line = {
        "aktiv": "Tagging-Modus: aktiv – ich tagge dich automatisch und schicke dir DMs.",
        "pausiert": "Tagging-Modus: pausiert – gerade keine Tags; mit /resume oder /start fortsetzen.",
        "aus": "Tagging-Modus: aus – mit /start einschalten, dann tagge ich dich bei Treffern.",
    }.get(state, state_text)

    lines: list[str] = []

    if not user_cfg:
        lines.append("Keine Daten von dir vorhanden.")
        lines.append(tag_line)
        lines.append("Starte mit /start und lege Regeln mit /add an. /help für alle Befehle.")
        return "\n\n".join([p for p in lines if p])

    lines.append(tag_line)

    gs = user_cfg.get("global_schedule")
    if gs:
        lines.append(f"Globales Zeitfenster: {describe_schedule(gs)} (nur dann tagge ich).")
    else:
        lines.append("Kein globales Zeitfenster – ich richte mich nach den Zeitfenstern deiner Regeln.")

    rules = user_cfg.get("rules") or []
    if not rules:
        lines.append("Keine angepassten Regeln – nutze /add für den Assistenten.")
    else:
        active_count = sum(1 for r in rules if not r.get("paused"))
        paused_count = sum(1 for r in rules if r.get("paused"))
        lines.append(f"{len(rules)} eingestellte Regeln (aktiv {active_count}, pausiert {paused_count}).")
        rule_lines = []
        for r in rules:
            rid = r.get("id") or "?"
            status_txt = "pausiert" if r.get("paused") else "aktiv"
            targets = ", ".join(r.get("targets") or ["alle"])
            insts = ", ".join(r.get("instances") or ["alle"])
            kws = ", ".join(r.get("keywords") or [])
            blocked = ", ".join(r.get("blocked_keywords") or [])
            sched = describe_schedule(r.get("schedule"))
            validity_txt = describe_validity(r.get("valid_from"), r.get("valid_until"))
            rule_lines.append(
                f"Regel {rid} ({status_txt})\n"
                f"Wörter: {kws or 'keine'}\n"
                f"Blockierte Wörter: {blocked or 'keine'}\n"
                f"Ziele: {targets}\n"
                f"Instanzen: {insts}\n"
                f"Zeitfenster: {sched}\n"
                f"Gültigkeit: {validity_txt}"
            )
        lines.append("Details zu deinen Regeln:\n\n" + "\n\n".join(rule_lines))

    lines.append("/help zeigt, wie du alles anpasst.")
    return "\n\n".join([p for p in lines if p])


def help_text() -> str:
    parts = [
        "öpnv_berlin_bot – dein kurzer Einstieg",
        "So nutzt du mich: Schicke Befehle per DM oder Mention, ich antworte per DM.",
        "/start: Tagging-Modus bewusst einschalten. Dann erwähne ich dich bei Treffern und schicke dir DMs.",
        "/add: Geführter Assistent für neue Regeln (Ziele, Stichworte, Zeiten).",
        "/overview: Kompakte Übersicht über Status und Regeln.",
        "/list: Alle Regeln ausführlich.",
        "/pause /resume: Alles pausieren oder wieder aktivieren. Mit ID steuerst du einzelne Regeln.",
        "/delete <id> oder /del <id>: Löscht eine Regel nach Rückfrage.",
        "/schedule: Assistent für ein globales Zeitfenster.",
        "/stop: Schaltet alles aus und löscht die Daten (nach Bestätigung).",
        "/status: Technischer Status (Twitter/Bluesky) und Fehlerzähler.",
        "/about: Infos zum Bot.",
        "/datenschutz (/privacy /data): Welche Daten ich speichere.",
        "Kurzform für Profis: /add <stichworte> [--block wort1,wort2] [--targets sbahn,viz,db,polizei,vbbvip|alle] "
        "[--time 06:00-22:00] [--days mo-fr|mo-sa|alle|fr|wochenende|...] [--skip-holidays yes/no] [--valid <zeitraum>].",
        "Offenen Dialog zurücksetzen: /startover."
    ]
    return "\n\n".join(parts)


def about_text() -> str:
    return (
        "Über den öpnv_berlin_bot\n\n"
        "Ich sammle ÖPNV-Meldungen (BVG/S-Bahn, VIZ, Bahn-Störung) von Twitter/X (über Nitter), Bluesky und ähnlichen Quellen und stelle sie auf Mastodon bereit.\n\n"
        "Die Mastodon-Posts laufen automatisch. Wenn du mich erwähnst, kannst du optionale Funktionen wie den Tagging-Modus nutzen: /start schaltet ihn ein, /stop aus. "
        "Ich tagge dich dann unter passenden Posts – nach deinen Stichworten, Blocklisten, Zeitfenstern und Gültigkeiten.\n\n"
        "Alles läuft lokal auf meinem Server; keine Cloud, keine Weitergabe. Quellcode & Feedback: GitHub https://github.com/Sam4000der2/selenium_twitter_Webcrawler_de"
    )


def privacy_text() -> str:
    return (
        "Datenschutz (Stand 02.01.2026)\n\n"
        "Dieser Bot repostet ausschließlich öffentlich zugängliche Meldungen von Twitter/X und Bluesky (u.a. S-Bahn, Deutsche Bahn, Polizei, Feuerwehr, VIZ Berlin sowie ggf. weitere öffentliche Accounts) nach Mastodon.\n\n"
        "Enthält ein Post Bilder, können diese zur automatischen Alt-Text-Erstellung an Gemini übermittelt werden; der erzeugte Alt-Text wird im Mastodon-Post hinterlegt.\n\n"
        "Interaktionen mit Mastodon-Nutzern werden nur verarbeitet, wenn die Funktion \"automatisches Taggen\" aktiv aktiviert wird. Dann speichere ich lokal den Mastodon-Handle und die gewählten Einstellungen. Ohne Aktivierung werden keine nutzerbezogenen Daten gespeichert.\n\n"
        "Löschung/Stop: /stop entfernt deine Regeln und schaltet den Modus aus; /delete <id> löscht einzelne Regeln.\n\n"
        "Kontakt/Löschung: @sam4000@troet.cafe"
    )


def tagging_mode_status(acct: str, data: dict | None = None) -> tuple[str, str]:
    src = data if data is not None else load_rules()
    users = src.get("users") or {}
    cfg = users.get(normalize_acct(acct))
    if not cfg:
        return "aus", "Tagging-Modus: aus (/start einschalten)"
    if cfg.get("global_pause"):
        return "pausiert", "Tagging-Modus: pausiert (/resume oder /start einschalten)"
    rules = cfg.get("rules") or []
    if not rules:
        return "aktiv", "Tagging-Modus: aktiv (keine Regeln gespeichert)"
    return "aktiv", "Tagging-Modus: aktiv"


# ----------------------------
# Dialog-States verarbeiten
# ----------------------------
def _state_key(instance_name: str, acct: str) -> str:
    return f"{instance_name}:{normalize_acct(acct)}"


def _set_state(instance_name: str, acct: str, state: dict):
    USER_STATES[_state_key(instance_name, acct)] = state


def _clear_state(instance_name: str, acct: str):
    USER_STATES.pop(_state_key(instance_name, acct), None)


def _build_schedule_from_state(data: dict) -> dict | None:
    tw = data.get("time_window")
    days_mode = data.get("days_mode") or "all"
    skip_holidays = data.get("skip_holidays", False)
    return compose_schedule(tw, days_mode, skip_holidays)


def _maybe_prompt_enable_tagging(mastodon, instance_name, acct, status_id, data: dict | None = None):
    state, _ = tagging_mode_status(acct, data)
    if state == "aktiv":
        return False
    USER_STATES[_state_key(instance_name, acct)] = {"mode": "confirm_enable_tagging", "tagging_state": state}
    if state == "pausiert":
        prompt = (
            "Der Tagging-Modus ist aktuell pausiert. Wenn er aktiv ist, erwähne ich dich automatisch unter passenden Posts "
            "und schicke dir eine DM, sobald deine Stichworte ausgelöst werden.\n\n"
            "Jetzt wieder einschalten? (ja/nein)"
        )
    else:
        prompt = (
            "Der Tagging-Modus ist aktuell aus. Wenn er aktiv ist, erwähne ich dich automatisch unter passenden Posts "
            "und schicke dir eine DM, sobald deine Stichworte ausgelöst werden. Du musst mir dafür nicht folgen, "
            "aber du musst den Modus bewusst einschalten.\n\n"
            "Jetzt aktivieren? (ja/nein)"
        )
    send_dm(
        mastodon,
        acct,
        status_id,
        prompt,
    )
    return True


async def _handle_confirm_enable_tagging(mastodon, instance_name, acct, status_id, text):
    state = USER_STATES.get(_state_key(instance_name, acct), {}).get("tagging_state", "aus")
    if _is_yes(text):
        cfg, data = ensure_user_config(acct)
        cfg["global_pause"] = False
        save_rules(data)
        _clear_state(instance_name, acct)
        send_dm(
            mastodon,
            acct,
            status_id,
            "Tagging-Modus aktiviert.\n\nIch tagge dich automatisch unter passende Posts (kein Follow nötig) und schicke dir dazu eine DM."
            if state != "pausiert"
            else "Tagging-Modus wieder aktiviert.\n\nIch tagge dich automatisch unter passende Posts (kein Follow nötig) und schicke dir dazu eine DM.",
        )
        return True
    if _is_no(text):
        _clear_state(instance_name, acct)
        if state == "pausiert":
            send_dm(mastodon, acct, status_id, "Okay, Tagging-Modus bleibt pausiert. Du kannst ihn jederzeit mit /resume oder /start einschalten.")
        else:
            send_dm(mastodon, acct, status_id, "Okay, Tagging-Modus bleibt aus. Du kannst ihn jederzeit mit /start einschalten.")
        return True
    send_dm(mastodon, acct, status_id, "Bitte antworte mit ja oder nein.")
    return True


async def _handle_confirm_start(mastodon, instance_name, acct, status_id, text):
    state = USER_STATES.get(_state_key(instance_name, acct), {}).get("tagging_state", "aus")
    if _is_yes(text):
        cfg, data = ensure_user_config(acct)
        cfg["global_pause"] = False
        save_rules(data)
        _clear_state(instance_name, acct)
        if state == "pausiert":
            msg = (
                "Tagging-Modus wieder aktiviert.\n\nIch erwähne dich unter passende Posts (kein Follow nötig) "
                "und schicke dir eine DM, wenn deine Stichworte passen. Lege jetzt Regeln mit /add an oder nutze /help."
            )
        else:
            msg = (
                "Tagging-Modus aktiviert.\n\nIch erwähne dich unter passende Posts (kein Follow nötig) "
                "und schicke dir eine DM, wenn deine Stichworte passen. Lege jetzt Regeln mit /add an oder nutze /help."
            )
        send_dm(mastodon, acct, status_id, msg)
        return True
    if _is_no(text):
        _clear_state(instance_name, acct)
        if state == "pausiert":
            send_dm(mastodon, acct, status_id, "Alles klar, Tagging bleibt pausiert. Melde dich mit /resume oder /start, wenn du es fortsetzen willst.")
        else:
            send_dm(mastodon, acct, status_id, "Alles klar, Tagging bleibt aus. Melde dich mit /start, wenn du es einschalten willst.")
        return True
    send_dm(mastodon, acct, status_id, "Bitte antworte mit ja oder nein.")
    return True


async def _handle_add_wizard(mastodon, instance_name, acct, status_id, text):
    state = USER_STATES.get(_state_key(instance_name, acct)) or {}
    step = state.get("step")
    data = state.setdefault("data", {})

    if step == "targets":
        val = text.lower().strip()
        if val in {"alle", "all"}:
            data["targets"] = ["alle"]
        else:
            chosen = []
            for part in val.split():
                key = part.strip()
                if key in ACCOUNT_GROUPS:
                    chosen.append(key)
            if not chosen:
                send_dm(
                    mastodon,
                    acct,
                    status_id,
                    "Ich habe dich nicht verstanden. Schreibe z.B. 'sbahn viz' oder 'polizei db' "
                    "oder einfach 'alle'. Gültige Gruppen: sbahn, viz, db, polizei, vbbvip.",
                    include_tagging_hint=False,
                )
                return True
            data["targets"] = chosen
        state["step"] = "keywords"
        send_dm(
            mastodon,
            acct,
            status_id,
            (
                "2) Welche Stichworte sollen auslösen?\n"
                "Bitte eine Zeile pro Stichwort, Beispiele:\n"
                "#S42\nAlexanderplatz\nSignalstörung\n\n"
                "Blockierte Wörter beginnst du mit '--', z.B. '--Werbung'.\n"
                "Alles, was du hier eingibst, suche ich später in den Posts. Groß-/Kleinschreibung ist egal."
            ),
            include_tagging_hint=False,
        )
        return True

    if step == "keywords":
        kws, blocked = parse_keywords_with_block(text)
        if not kws:
            send_dm(
                mastodon,
                acct,
                status_id,
                "Mindestens ein Stichwort wird gebraucht. Markiere optionale Ausschlüsse mit '--Stichwort' oder '- - Stichwort'.",
                include_tagging_hint=False,
            )
            return True
        data["keywords"] = kws
        data["blocked_keywords"] = blocked
        state["step"] = "time"
        send_dm(
            mastodon,
            acct,
            status_id,
            (
                "3) Zeitfenster: Wann sollen die Regeln aktiv sein?\n"
                "- Beispiel: 06:00-22:00\n"
                "- Oder 'immer' für rund um die Uhr\n"
                "Schreib die Zeiten als HH:MM-HH:MM. Beispiel: 07:00-18:00."
            ),
            include_tagging_hint=False,
        )
        return True

    if step == "time":
        t = text.lower().strip()
        if t in {"immer", "all", "standard", ""}:
            data["time_window"] = None
        else:
            win = parse_time_window(text)
            if not win:
                send_dm(
                    mastodon,
                    acct,
                    status_id,
                    "Format nicht verstanden. Bitte schreib Zeiten als HH:MM-HH:MM, z.B. 07:00-18:00. "
                    "Oder 'immer' für den ganzen Tag.",
                    include_tagging_hint=False,
                )
                return True
            data["time_window"] = win
        state["step"] = "days"
        send_dm(
            mastodon,
            acct,
            status_id,
            (
                "4) Für welche Tage gilt die Regel?\n"
                "- mo-fr / mo-sa / alle\n"
                "- Einzelne Tage: mo, di, mi, do, fr, sa, so\n"
                "- Wochenende: wochenende\n"
                "Beispiele: 'fr' für nur Freitag, 'wochenende' für Sa+So.\n"
                "Wenn du nichts Besonderes brauchst, antworte einfach mit 'alle'."
            ),
            include_tagging_hint=False,
        )
        return True

    if step == "days":
        dm = parse_days_mode(text) or "all"
        data["days_mode"] = dm
        state["step"] = "holidays"
        send_dm(
            mastodon,
            acct,
            status_id,
            (
                "5) Berliner Feiertage überspringen? (ja/nein)\n"
                "Wenn du 'ja' antwortest, pausiert die Regel an Berliner Feiertagen. 'nein' = auch an Feiertagen aktiv."
            ),
            include_tagging_hint=False,
        )
        return True

    if step == "holidays":
        skip = _is_yes(text)
        data["skip_holidays"] = skip
        state["step"] = "validity"
        _set_state(instance_name, acct, state)
        send_dm(
            mastodon,
            acct,
            status_id,
            (
                "6) Optional: Gültigkeit der Regel. Leer lassen für dauerhaft.\n"
                "- Beispiele: 'bis 2025-02-01', 'für 3 Tage', '4 Wochen lang',\n"
                "  '01.02.2025-10.02.2025', 'in 2 Wochen'."
            ),
            include_tagging_hint=False,
        )
        return True

    if step == "validity":
        valid_from, valid_until, err = parse_validity_window(text)
        if err:
            send_dm(
                mastodon,
                acct,
                status_id,
                f"{err}\n\nBeispiele: 'bis 2025-02-01', 'für 3 Tage', '4 Wochen lang', 'in 2 Wochen'.",
                include_tagging_hint=False,
            )
            return True
        schedule = _build_schedule_from_state(data)
        validity_txt = describe_validity(valid_from, valid_until)
        summary = (
            "Neue Regel (bitte bestätigen):\n"
            f"Instanzen: {instance_name}\n"
            "Ziele (Accounts):\n"
            + "\n".join([f"- {t}" for t in (data.get("targets") or ['alle'])])
            + "\nStichworte:\n"
            + "\n".join([f"- {k}" for k in data.get("keywords", [])])
            + "\nBlockierte Stichworte:\n"
            + "\n".join([f"- {k}" for k in data.get("blocked_keywords", [])] or ["- keine"])
            + f"\nZeitfenster:\n{describe_schedule(schedule)}\n"
            + f"Gültigkeit:\n{validity_txt}\n"
            "Passt das so? Antworte mit 'ja' zum Speichern oder 'nein' zum Abbrechen."
        )
        state["step"] = "confirm"
        data["valid_from"] = valid_from
        data["valid_until"] = valid_until
        _set_state(instance_name, acct, state)
        send_dm(
            mastodon,
            acct,
            status_id,
            summary,
            include_tagging_hint=False,
        )
        return True

    if step == "confirm":
        if _is_yes(text):
            data = state.get("data", {})
            schedule = _build_schedule_from_state(data)
            vf = _date_from_field(data.get("valid_from"))
            vu = _date_from_field(data.get("valid_until"))
            cfg, full = ensure_user_config(acct)
            rule = {
                "id": _next_rule_id(),
                "keywords": data.get("keywords", []),
                "blocked_keywords": data.get("blocked_keywords", []),
                "targets": data.get("targets") or ["alle"],
                "instances": [instance_name],
                "schedule": schedule,
                "valid_from": vf.isoformat() if vf else None,
                "valid_until": vu.isoformat() if vu else None,
                "paused": False,
            }
            cfg.setdefault("rules", []).append(rule)
            save_rules(full)
            send_dm(
                mastodon,
                acct,
                status_id,
                f"Regel gespeichert. ID: {rule['id']}\n"
                f"Zeitfenster: {describe_schedule(schedule)}\n"
                f"Gültigkeit: {describe_validity(rule.get('valid_from'), rule.get('valid_until'))}\n"
                "Mit /list siehst du alle Regeln. Mit /add kannst du weitere Regeln anlegen.",
                include_tagging_hint=False,
            )
            _clear_state(instance_name, acct)
            _maybe_prompt_enable_tagging(mastodon, instance_name, acct, status_id, full)
        elif _is_no(text):
            send_dm(
                mastodon,
                acct,
                status_id,
                "Abgebrochen.\n\nNutze /add für einen neuen Versuch.",
                include_tagging_hint=False,
            )
            _clear_state(instance_name, acct)
        else:
            send_dm(
                mastodon,
                acct,
                status_id,
                "Bitte antworte mit ja oder nein.",
                include_tagging_hint=False,
            )
        return True

    return False


async def _handle_confirm_action(mastodon, instance_name, acct, status_id, text):
    state = USER_STATES.get(_state_key(instance_name, acct)) or {}
    mode = state.get("mode")
    action = state.get("action")
    target = state.get("target")
    targets = state.get("targets")
    if target and not targets:
        targets = [target]

    if mode not in {"confirm_action"}:
        return False

    if _is_yes(text):
        cfg, full = ensure_user_config(acct)
        keep_state = False
        if action in {"delete_rule", "delete_rules"}:
            rules = cfg.get("rules", [])
            target_ids = targets or []
            cfg["rules"] = [r for r in rules if r.get("id") not in target_ids]
            save_rules(full)
            deleted_label = ", ".join(target_ids) if target_ids else str(target)
            send_dm(mastodon, acct, status_id, f"Regel(n) {deleted_label} gelöscht.\n\nMit /list oder /overview siehst du den aktuellen Stand.")
        elif action == "pause_all":
            cfg["global_pause"] = True
            save_rules(full)
            send_dm(mastodon, acct, status_id, "Alle Regeln pausiert.\n\nMit /resume machst du alles wieder aktiv.")
        elif action == "resume_all":
            cfg["global_pause"] = False
            save_rules(full)
            send_dm(mastodon, acct, status_id, "Alle Regeln wieder aktiv.\n\nDu kannst einzelne Regeln mit /pause <id> pausieren.")
        elif action in {"pause_rule", "pause_rules"}:
            target_ids = targets or []
            for r in cfg.get("rules", []):
                if r.get("id") in target_ids:
                    r["paused"] = True
            save_rules(full)
            paused_label = ", ".join(target_ids) if target_ids else str(target)
            send_dm(mastodon, acct, status_id, f"Regel {paused_label} pausiert.\n\nMit /resume <id> aktivierst du sie wieder.")
        elif action in {"resume_rule", "resume_rules"}:
            target_ids = targets or []
            for r in cfg.get("rules", []):
                if r.get("id") in target_ids:
                    r["paused"] = False
            cfg["global_pause"] = False
            save_rules(full)
            resumed_label = ", ".join(target_ids) if target_ids else str(target)
            send_dm(mastodon, acct, status_id, f"Regel {resumed_label} wieder aktiv.\n\nWenn alles pausiert war, ist der Tagging-Modus jetzt auch wieder aktiv.")
        elif action == "stop_all":
            cfg.clear()
            full["users"].pop(normalize_acct(acct), None)
            save_rules(full)
            send_dm(mastodon, acct, status_id, "Tagging deaktiviert und alle Regeln entfernt.\n\nDu kannst jederzeit mit /start neu beginnen und mit /add Regeln anlegen.")
        elif action == "set_global_schedule":
            sched = state.get("schedule")
            cfg["global_schedule"] = sched
            cfg["global_pause"] = False
            save_rules(full)
            send_dm(mastodon, acct, status_id, f"Globales Zeitfenster gesetzt: {describe_schedule(sched)}.\n\nEs gilt für alle Regeln.")
        elif action == "confirm_add_quick":
            pending = state.get("pending_rule")
            if pending:
                cfg.setdefault("rules", []).append(pending)
                save_rules(full)
                sched_txt = describe_schedule(pending.get("schedule"))
                validity_txt = describe_validity(pending.get("valid_from"), pending.get("valid_until"))
                send_dm(
                    mastodon,
                    acct,
                    status_id,
                    f"Regel gespeichert. ID: {pending.get('id')}\nZeitfenster: {sched_txt}\nGültigkeit: {validity_txt}\n\n/overview zeigt dir eine Kurzfassung."
                )
                keep_state = _maybe_prompt_enable_tagging(mastodon, instance_name, acct, status_id, full)
            else:
                send_dm(mastodon, acct, status_id, "Keine Regel in der Warteschlange. Schicke /add erneut, um eine Regel anzulegen.")
        if not keep_state:
            _clear_state(instance_name, acct)
        return True

    if _is_no(text):
        send_dm(mastodon, acct, status_id, "Abgebrochen.\n\nWenn du es erneut versuchen möchtest, sende den Befehl einfach nochmal oder nutze /help.")
        _clear_state(instance_name, acct)
        return True

    send_dm(mastodon, acct, status_id, "Bitte antworte mit ja oder nein.")
    return True


async def _handle_schedule_wizard(mastodon, instance_name, acct, status_id, text):
    state = USER_STATES.get(_state_key(instance_name, acct)) or {}
    step = state.get("step")
    data = state.setdefault("data", {})

    if step == "time":
        t = text.lower().strip()
        if t in {"immer", "all", "standard", ""}:
            data["time_window"] = None
        else:
            win = parse_time_window(text)
            if not win:
                send_dm(mastodon, acct, status_id, "Format nicht verstanden. Nutze HH:MM-HH:MM oder 'immer'.")
                return True
            data["time_window"] = win
        state["step"] = "days"
        send_dm(
            mastodon,
            acct,
            status_id,
            "Welche Tage?\n\n- mo-fr / mo-sa / alle\n- Einzelne Tage: mo, di, mi, do, fr, sa, so\n- Wochenende: wochenende"
        )
        return True

    if step == "days":
        dm = parse_days_mode(text) or "all"
        data["days_mode"] = dm
        state["step"] = "holidays"
        send_dm(mastodon, acct, status_id, "Berliner Feiertage überspringen? (ja/nein)")
        return True

    if step == "holidays":
        skip = _is_yes(text)
        data["skip_holidays"] = skip
        schedule = _build_schedule_from_state(data)
        USER_STATES[_state_key(instance_name, acct)] = {
            "mode": "confirm_action",
            "action": "set_global_schedule",
            "schedule": schedule,
        }
        send_dm(mastodon, acct, status_id, f"Globales Zeitfenster setzen auf: {describe_schedule(schedule)}? (ja/nein)")
        return True

    return False


async def handle_pending_state(mastodon, instance_name, acct, status_id, text) -> bool:
    state = USER_STATES.get(_state_key(instance_name, acct))
    if not state:
        return False

    mode = state.get("mode")
    if mode == "confirm_start":
        return await _handle_confirm_start(mastodon, instance_name, acct, status_id, text)
    if mode == "confirm_enable_tagging":
        return await _handle_confirm_enable_tagging(mastodon, instance_name, acct, status_id, text)
    if mode == "add_wizard":
        return await _handle_add_wizard(mastodon, instance_name, acct, status_id, text)
    if mode == "confirm_action":
        return await _handle_confirm_action(mastodon, instance_name, acct, status_id, text)
    if mode == "schedule_wizard":
        return await _handle_schedule_wizard(mastodon, instance_name, acct, status_id, text)
    return False


# ----------------------------
# Quick-Add Parser
# ----------------------------
def parse_quick_add_args(parts: list[str]) -> tuple[
    list[str],
    list[str],
    dict | None,
    tuple[date | None, date | None],
    str,
    list[str]
]:
    keywords_raw: list[str] = []
    blocked_raw: list[str] = []
    time_window = None
    days_mode = "all"
    skip_holidays = False
    targets: list[str] | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    errors = []

    def collect_value(start_idx: int) -> tuple[str, int]:
        vals: list[str] = []
        i = start_idx
        while i < len(parts) and not parts[i].startswith("--"):
            vals.append(parts[i])
            i += 1
        joined = " ".join(vals).strip()
        return joined, i

    idx = 0
    while idx < len(parts):
        p = parts[idx]
        if p.startswith("--time"):
            candidate, next_idx = collect_value(idx + 1)
            if candidate:
                time_window = parse_time_window(candidate)
                if candidate.lower() in {"immer", "all"}:
                    time_window = None
                if time_window is None and candidate.lower() not in {"immer", "all"}:
                    errors.append("Zeitfenster-Format bitte HH:MM-HH:MM oder 'immer'")
            idx = max(next_idx, idx + 1)
            continue
        if p.startswith("--days"):
            candidate, next_idx = collect_value(idx + 1)
            dm = parse_days_mode(candidate)
            if dm:
                days_mode = dm
            idx = max(next_idx, idx + 1)
            continue
        if p.startswith("--skip-holidays"):
            candidate, next_idx = collect_value(idx + 1)
            skip_holidays = _is_yes(candidate)
            idx = max(next_idx, idx + 1)
            continue
        if p.startswith("--targets"):
            tgt_raw, next_idx = collect_value(idx + 1)
            if tgt_raw.lower() in {"alle", "all"}:
                targets = ["alle"]
            else:
                chosen = []
                for t in tgt_raw.split(","):
                    t_key = t.strip().lower()
                    if t_key in ACCOUNT_GROUPS:
                        chosen.append(t_key)
                if not chosen:
                    errors.append("Mindestens ein gültiges Target: sbahn,viz,db,polizei,vbbvip oder 'alle'")
                else:
                    targets = chosen
            idx = max(next_idx, idx + 1)
            continue
        if p.startswith("--block"):
            block_raw, next_idx = collect_value(idx + 1)
            if block_raw:
                cleaned = parse_keywords(block_raw.replace(",", " "))
                blocked_raw.extend(cleaned)
            idx = max(next_idx, idx + 1)
            continue
        if p.startswith("--valid"):
            valid_raw, next_idx = collect_value(idx + 1)
            vf, vu, err = parse_validity_window(valid_raw)
            if err:
                errors.append(err)
            else:
                valid_from = vf
                valid_until = vu
            idx = max(next_idx, idx + 1)
            continue
        if p.startswith("--"):
            errors.append(f"Unbekannte Option {p}")
            idx += 1
            continue
        keywords_raw.append(p)
        idx += 1

    if not keywords_raw:
        errors.append("Mindestens ein Stichwort angeben.")

    keywords = parse_keywords(" ".join(keywords_raw))
    blocked_keywords = parse_keywords(" ".join(blocked_raw))

    # Blockierte Stichworte haben Vorrang – entferne doppelte
    blocked_set = {b.lower() for b in blocked_keywords}
    keywords = [k for k in keywords if k.lower() not in blocked_set]

    schedule = compose_schedule(time_window, days_mode, skip_holidays)
    err = "; ".join(errors)
    return keywords, blocked_keywords, schedule, (valid_from, valid_until), err, (targets or ["alle"])


def get_user_config_view(acct: str) -> tuple[dict | None, dict]:
    data = load_rules()
    users = data.get("users") or {}
    return users.get(normalize_acct(acct)), data


def parse_rule_ids_from_text(text: str) -> list[str]:
    ids = []
    for rid in re.findall(r"r\d+", text):
        if rid not in ids:
            ids.append(rid)
    return ids


def format_rule_id_overview(cfg: dict | None) -> str:
    rules = cfg.get("rules", []) if cfg else []
    if not rules:
        return "Keine Regeln gefunden. Lege mit /add eine Regel an."

    lines = ["Deine Regeln:"]
    for r in rules:
        rid = r.get("id") or "?"
        kws = ", ".join(r.get("keywords") or [])
        status_txt = "pausiert" if r.get("paused") else "aktiv"
        lines.append(f"- {rid} ({status_txt}): {kws or 'keine Stichworte'}")
    return "\n".join(lines)


# ----------------------------
# Command Handling
# ----------------------------
async def handle_command(mastodon, instance_name, status, account):
    acct = account.get("acct") or ""
    status_id = status.get("id")
    if not acct or status_id is None:
        return

    text = clean_command_text(status.get("content") or "")
    cmd, remainder = parse_user_command(text)
    lower = text.lower()

    # Falls noch ein Dialog offen ist, zuerst dort antworten
    if await handle_pending_state(mastodon, instance_name, acct, status_id, text):
        return

    if cmd == "status" or lower.startswith("/status"):
        reply_text = build_status_text()
        send_dm(mastodon, acct, status_id, reply_text)
        return

    if cmd == "help" or lower.startswith("/help") or lower.startswith("/hilfe"):
        send_dm(mastodon, acct, status_id, help_text())
        return

    if cmd == "about" or lower.startswith("/about"):
        send_dm(mastodon, acct, status_id, about_text())
        return

    if cmd == "datenschutz" or lower.startswith("/datenschutz") or lower.startswith("/privacy") or lower.startswith("/data"):
        send_dm(mastodon, acct, status_id, privacy_text())
        return

    if cmd == "start" or lower.startswith("/start"):
        state, _ = tagging_mode_status(acct)
        if state == "aktiv":
            send_dm(
                mastodon,
                acct,
                status_id,
                "Tagging-Modus ist schon aktiv.\n\nNutze /add für Regeln oder /list für eine Übersicht."
            )
            return

        _set_state(instance_name, acct, {"mode": "confirm_start", "tagging_state": state})
        if state == "pausiert":
            prompt = (
                "Dein Tagging-Modus ist pausiert. Ich kann dich automatisch unter passende Posts erwähnen und dir eine DM schicken, "
                "wenn deine Stichworte auftauchen.\n\n"
                "Wieder einschalten? Antworte mit 'ja' oder 'nein'."
            )
        else:
            prompt = (
                "Ich kann dich automatisch unter passende Posts erwähnen und dir eine DM schicken, wenn deine Stichworte auftauchen. "
                "Du musst mir nicht folgen, aber du musst den Tagging-Modus bewusst einschalten. Mit /status siehst du, ob die Module laufen.\n\n"
                "Tagging-Modus jetzt aktivieren? Antworte mit 'ja' oder 'nein'."
            )
        send_dm(mastodon, acct, status_id, prompt)
        return

    if cmd == "add" or lower.startswith("/add"):
        base = remainder if cmd == "add" else " ".join(text.split()[1:])
        parts = base.split() if base else []
        if parts:
            kws, blocked, sched, validity, err, targets = parse_quick_add_args(parts)
            if err:
                send_dm(
                    mastodon,
                    acct,
                    status_id,
                    f"Konnte die Kurzform nicht verstehen: {err}\n\nTipp: Nutze /add ohne Optionen für den Assistenten oder sieh dir /help an."
                )
                return
            vf, vu = validity
            summary = (
                "Neue Regel (Kurzform) – bitte bestätigen:\n\n"
                "Stichworte:\n"
                + "\n".join([f"- {k}" for k in kws])
                + "\n\nBlockierte Stichworte:\n"
                + "\n".join([f"- {k}" for k in blocked] or ["- keine"])
                + "\n\nZiele:\n"
                + "\n".join([f"- {t}" for t in (targets or ['alle'])])
                + f"\n\nInstanzen:\n- {instance_name}\n\n"
                + f"Zeitfenster:\n{describe_schedule(sched)}\n\n"
                + f"Gültigkeit:\n{describe_validity(vf, vu)}\n\n"
                "Speichern? (ja/nein)"
            )
            USER_STATES[_state_key(instance_name, acct)] = {
                "mode": "confirm_action",
                "action": "confirm_add_quick",
                "pending_rule": {
                    "id": _next_rule_id(),
                    "keywords": kws,
                    "blocked_keywords": blocked,
                    "targets": targets or ["alle"],
                    "instances": [instance_name],
                    "schedule": sched,
                    "valid_from": vf.isoformat() if vf else None,
                    "valid_until": vu.isoformat() if vu else None,
                    "paused": False,
                }
            }
            send_dm(mastodon, acct, status_id, summary)
            return

        _set_state(instance_name, acct, {"mode": "add_wizard", "step": "targets", "data": {}})
        send_dm(
            mastodon,
            acct,
            status_id,
            "Lass uns eine Regel anlegen. Ich stelle kurze Fragen und richte alles für dich ein.\n\n"
            "1) Für welche Accounts soll ich dich taggen?\n"
            "- sbahn (SBahnBerlin)\n"
            "- viz (VIZ_Berlin)\n"
            "- db (DBRegio_BB)\n"
            "- polizei (polizeiberlin/bpol/Berliner_FW)\n"
            "- vbbvip (vbb/VIP)\n"
            "So antwortest du: 'sbahn viz' (mehrere getrennt durch Leerzeichen) oder 'alle' für alles.",
            include_tagging_hint=False,
        )
        return

    if cmd == "list" or lower.startswith("/list"):
        summary = format_rules_summary(acct)
        send_dm(mastodon, acct, status_id, summary)
        return

    if cmd == "overview" or lower.startswith("/overview"):
        summary = format_rules_overview(acct)
        send_dm(mastodon, acct, status_id, summary, include_tagging_hint=False)
        return

    if cmd == "delete" or lower.startswith("/delete") or lower.startswith("/del"):
        cfg, _ = get_user_config_view(acct)
        if not cfg or not cfg.get("rules"):
            send_dm(mastodon, acct, status_id, "Keine Regeln gefunden. Lege mit /add eine Regel an.")
            return

        id_text = remainder if cmd == "delete" else " ".join(text.split()[1:])
        ids = parse_rule_ids_from_text(id_text)
        all_ids = {r.get("id") for r in cfg.get("rules", [])}

        if not ids or any(rid not in all_ids for rid in ids):
            overview = format_rule_id_overview(cfg)
            send_dm(
                mastodon,
                acct,
                status_id,
                f"Bitte gib eine gültige Regel-ID an: /delete <id>\n\n{overview}",
            )
            return

        USER_STATES[_state_key(instance_name, acct)] = {
            "mode": "confirm_action",
            "action": "delete_rules",
            "targets": ids,
        }
        send_dm(
            mastodon,
            acct,
            status_id,
            f"Regel(n) {', '.join(ids)} wirklich löschen? (ja/nein)"
        )
        return

    if cmd == "pause" or lower.startswith("/pause") or lower.startswith("/disable"):
        cfg, _ = get_user_config_view(acct)
        id_text = remainder if cmd == "pause" else " ".join(text.split()[1:])
        ids = parse_rule_ids_from_text(id_text)
        existing_ids = {r.get("id") for r in (cfg.get("rules") if cfg else [])}

        if ids:
            if not existing_ids or any(rid not in existing_ids for rid in ids):
                overview = format_rule_id_overview(cfg)
                send_dm(mastodon, acct, status_id, f"Bitte nutze eine gültige Regel-ID.\n\n{overview}")
                return
            USER_STATES[_state_key(instance_name, acct)] = {
                "mode": "confirm_action",
                "action": "pause_rules",
                "targets": ids
            }
            send_dm(mastodon, acct, status_id, f"Regel(n) {', '.join(ids)} pausieren? (ja/nein)")
        else:
            USER_STATES[_state_key(instance_name, acct)] = {
                "mode": "confirm_action",
                "action": "pause_all"
            }
            send_dm(mastodon, acct, status_id, "Alle Regeln pausieren? (ja/nein)")
        return

    if cmd == "resume" or lower.startswith("/resume") or lower.startswith("/enable"):
        cfg, _ = get_user_config_view(acct)
        id_text = remainder if cmd == "resume" else " ".join(text.split()[1:])
        ids = parse_rule_ids_from_text(id_text)
        existing_ids = {r.get("id") for r in (cfg.get("rules") if cfg else [])}

        if ids:
            if not existing_ids or any(rid not in existing_ids for rid in ids):
                overview = format_rule_id_overview(cfg)
                send_dm(mastodon, acct, status_id, f"Bitte nutze eine gültige Regel-ID.\n\n{overview}")
                return
            USER_STATES[_state_key(instance_name, acct)] = {
                "mode": "confirm_action",
                "action": "resume_rules",
                "targets": ids
            }
            send_dm(mastodon, acct, status_id, f"Regel(n) {', '.join(ids)} wieder aktivieren? (ja/nein)")
        else:
            USER_STATES[_state_key(instance_name, acct)] = {
                "mode": "confirm_action",
                "action": "resume_all"
            }
            send_dm(mastodon, acct, status_id, "Alle Regeln aktivieren? (ja/nein)")
        return

    if cmd == "schedule" or lower.startswith("/schedule"):
        _set_state(instance_name, acct, {"mode": "schedule_wizard", "step": "time", "data": {}})
        send_dm(
            mastodon,
            acct,
            status_id,
            "Globales Zeitfenster einstellen:\n\n"
            "- Sende eine Zeitspanne als HH:MM-HH:MM (z.B. 06:00-22:00)\n"
            "- oder 'immer' für rund um die Uhr."
        )
        return

    if cmd == "stop" or lower.startswith("/stop"):
        USER_STATES[_state_key(instance_name, acct)] = {
            "mode": "confirm_action",
            "action": "stop_all"
        }
        send_dm(mastodon, acct, status_id, "Tagging komplett deaktivieren und alle Regeln löschen? (ja/nein)")
        return

    if cmd == "startover" or lower.startswith("/startover"):
        _clear_state(instance_name, acct)
        send_dm(mastodon, acct, status_id, "Dialog zurückgesetzt. Nutze /add oder /help.")
        return

    send_dm(
        mastodon,
        acct,
        status_id,
        "Das habe ich nicht verstanden.\n\nNutze /help für eine sehr einfache Anleitung.\n"
        "Kurz: /start (einschalten), /add (Assistent), /list (Regeln anzeigen)."
    )


async def process_notification(mastodon, instance_name, notif, self_id):
    status = notif.get("status") or {}
    if not status:
        return

    account = status.get("account") or {}
    if account.get("id") == self_id:
        return

    # Reagiere auf Befehle, egal ob DM oder öffentlich, Antworten immer per DM
    try:
        await handle_command(mastodon, instance_name, status, account)
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler in handle_command ({instance_name}): {e}")


# ----------------------------
# Bot Loop
# ----------------------------
async def _run_instance(instance_name: str, cfg: dict):
    token_env = cfg.get("access_token_env")
    base_url = cfg.get("api_base_url")
    access_token = os.environ.get(token_env or "")
    if not access_token:
        logging.error(f"mastodon_control_bot: Kein Access-Token in ENV '{token_env}' für {instance_name}")
        return

    try:
        mastodon = Mastodon(
            access_token=access_token,
            api_base_url=base_url,
        )
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler beim Initialisieren des Mastodon-Clients ({instance_name}): {e}")
        return

    try:
        me = mastodon.account_verify_credentials()
        self_id = me["id"]
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler bei account_verify_credentials ({instance_name}): {e}")
        return

    INSTANCE_CLIENTS[instance_name] = mastodon

    since_id = None
    try:
        recent = mastodon.notifications(limit=1)
        if recent:
            since_id = recent[0]["id"]
    except Exception as e:
        logging.error(f"mastodon_control_bot: Fehler beim Lesen der letzten Notification ({instance_name}): {e}")

    try:
        while True:
            try:
                notifications = await asyncio.to_thread(mastodon.notifications, since_id=since_id)
                for notif in reversed(notifications):
                    notif_id = notif.get("id")
                    if notif_id is not None:
                        since_id = max(since_id or notif_id, notif_id)
                    try:
                        await process_notification(mastodon, instance_name, notif, self_id)
                    except Exception as e:
                        logging.error(f"mastodon_control_bot: Fehler beim Verarbeiten einer Notification ({instance_name}): {e}")
            except KeyboardInterrupt:
                # Sanft beenden ohne Traceback-Spam
                return
            except Exception as e:
                logging.error(f"mastodon_control_bot: Fehler beim Abfragen von Notifications ({instance_name}): {e}")

            await asyncio.sleep(POLL_INTERVAL_SEC)
    finally:
        INSTANCE_CLIENTS.pop(instance_name, None)


async def start_bot():
    tasks = []
    if EVENT_ENABLED:
        tasks.append(asyncio.create_task(start_event_listener()))
    for name, cfg in INSTANCES.items():
        tasks.append(asyncio.create_task(_run_instance(name, cfg)))
    if not tasks:
        logging.error("mastodon_control_bot: Keine Instanz-Tasks gestartet.")
        return
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        # Graceful shutdown
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(start_bot())
