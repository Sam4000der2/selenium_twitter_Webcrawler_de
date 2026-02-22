import telegram_bot
import mastodon_bot
import asyncio
import os
import telegram
from datetime import datetime, timedelta
import logging
import subprocess
import re
from mastodon_text_utils import split_mastodon_text
import state_store

# Secrets aus ENV (global + local):
#   telegram_token  -> Bot Token
#   telegram_admin  -> Admin Chat-ID
BOT_TOKEN = os.environ.get("telegram_token")
admin_env = os.environ.get("telegram_admin")

# Admin-Chat-ID robust parsen (Telegram liefert ints)
try:
    admin = int(admin_env) if admin_env is not None and str(admin_env).strip() != "" else None
except Exception:
    admin = None

# Configure logging (für dieses Script)
logging.basicConfig(
    filename='/home/sascha/bots/twitter_bot.log',
    level=logging.WARNING,
    format='%(asctime)s %(levelname)s:%(message)s'
)

# Zentrales Bot-Logfile (alle Module schreiben hier rein)
BOT_LOG_FILE = '/home/sascha/bots/twitter_bot.log'

# Bots für Fehlerzählung (letzte 24h) im /status
ALT_TEXT_CATEGORY = "Alt-Text Generierung"
GEMINI_HELPER_CATEGORY = "gemini_helper"

BOT_NAMES_FOR_COUNT = [
    "twitter_bot",
    "telegram_bot",
    "mastodon_bot",
    "bsky_bot",
    "nitter_bot",
]

ADMIN_BOT_NAMES_FOR_COUNT = BOT_NAMES_FOR_COUNT + [
    "mastodon_control_bot",
    "telegram_control_bot",
    ALT_TEXT_CATEGORY,
    GEMINI_HELPER_CATEGORY,
]

# Fixe Gruppen für /errors (admin) + nicht_zuordenbar
ADMIN_ERROR_GROUPS = [
    "twitter_bot",
    "telegram_bot",
    "mastodon_bot",
    "bsky_bot",
    "nitter_bot",
    "mastodon_control_bot",
    "telegram_control_bot",
    "gemini_helper",
    ALT_TEXT_CATEGORY,
    "nicht_zuordenbar",
]

# /errors nur letzte X Tage berücksichtigen
ADMIN_ERRORS_DAYS = 3

BUS_ERROR_MARKERS = (
    "Failed to connect to bus",
    "Kein Medium gefunden",
    "System has not been booted with systemd",
    "Failed to connect to system bus",
)

# Zeitstempel am Anfang der Zeile
TS_RX = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})\s+(?P<rest>.*)$")

if not BOT_TOKEN:
    logging.error("telegram_control_bot: ENV 'telegram_token' ist nicht gesetzt.")
if admin is None:
    logging.error("telegram_control_bot: ENV 'telegram_admin' ist nicht gesetzt oder keine gültige Zahl.")


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
    """
    Fallback-Check ohne systemd: schaut ob ein Prozess läuft.
    patterns: Suchstrings für pgrep -f
    """
    my_pid = str(os.getpid())

    for pat in patterns:
        rc, out = _run_cmd_no_sudo(["pgrep", "-fa", pat], timeout=2)
        if rc == 0 and out:
            for line in out.splitlines():
                # eigenes PID rausfiltern
                if my_pid in line:
                    continue
                # optional: falls dein Control-Bot-Name im cmd auftaucht, rausfiltern
                if "telegram_control_bot" in line:
                    continue
                return True, line.strip()

    return False, ""


def get_service_state(service_name: str, fallback_patterns: list[str]) -> tuple[str, str]:
    """
    Gibt (status_kategorie, details) zurück.
    status_kategorie: "läuft" | "gestoppt" | "abgestürzt" | "gestoppt/abgestürzt" | "unbekannt"
    Alles ohne sudo (Userrechte).
    """
    candidates = [
        ["systemctl", "--user", "is-active", service_name],
        ["systemctl", "is-active", service_name],
    ]

    bus_error = False
    last = "unknown"

    for cmd in candidates:
        rc, out = _run_cmd_no_sudo(cmd)
        last = (out or "").strip() or last

        if _looks_like_bus_error(last):
            bus_error = True
            continue

        if last == "active":
            return "läuft", f"systemctl:{last}"
        if last == "inactive":
            return "gestoppt", f"systemctl:{last}"
        if last == "failed":
            return "abgestürzt", f"systemctl:{last}"

        if last:
            return "unbekannt", f"systemctl:{last}"

    if bus_error:
        running, hit = _pgrep_any(fallback_patterns)
        if running:
            return "läuft", f"fallback:prozess ({hit})"
        return "gestoppt/abgestürzt", "fallback:kein prozess (systemd/dbus nicht erreichbar)"

    return "unbekannt", f"systemctl:{last}"


# ----------------------------
# Helpers: Telegram Message Split
# ----------------------------

def split_telegram_text(text: str, max_len: int = 3900) -> list[str]:
    parts = []
    remaining = text.strip()
    while remaining:
        if len(remaining) <= max_len:
            parts.append(remaining)
            break
        chunk = remaining[:max_len]
        split_at = max(chunk.rfind("\n\n"), chunk.rfind("\n"), chunk.rfind(". "), chunk.rfind(", "))
        if split_at <= 0:
            split_at = max_len
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return parts


def _trim_long(s: str, max_len: int = 800) -> str:
    s = (s or "").strip()
    return s if len(s) <= max_len else s[: max_len - 1] + "…"


# ----------------------------
# Helpers: Reverse File Reader (für große Logs)
# ----------------------------

def _reverse_read_lines(path: str, block_size: int = 8192):
    """
    Generator: liefert Zeilen rückwärts (neueste zuerst), ohne die ganze Datei zu laden.
    """
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


# ----------------------------
# Log Parsing: Botnamen suchen + danach gruppieren (robust)
# ----------------------------

def parse_ts_and_rest(line: str) -> tuple[datetime | None, str]:
    """
    Erwartet: 'YYYY-MM-DD HH:MM:SS,mmm <rest>'
    Gibt (ts, rest) zurück.
    """
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
    """
    Sucht nach '<bot_name>:' im rest.
    Gibt (group, pure_message_after_botname) zurück.
    Falls kein bot_name gefunden: ('nicht_zuordenbar', rest)
    """
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

    msg = r[best_idx + len(best_bot) + 1:].strip()  # +1 für ':'
    if not msg:
        msg = "(leer)"
    return best_bot, msg


def count_errors_since_grouped(
    log_path: str,
    bots: list[str],
    since_dt: datetime,
    levels: tuple[str, ...] = ("ERROR",)
) -> dict[str, dict[str, int]]:
    """
    Ergebnis: { bot: { reine_fehlermeldung: count, ... }, ... }
    Reine Fehlermeldung = alles NACH "<botname>:"
    Zählt nur Einträge mit passendem Level und nur Zeilen >= since_dt.
    """
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


def read_last_errors_grouped(
    log_path: str,
    per_group: int = 3,
    days: int = 3,
    levels: tuple[str, ...] = ("ERROR",)
) -> dict[str, list[str]]:
    """
    Admin: letzte ERROR-Zeilen, gruppiert nach festen Gruppen:
      twitter_bot, telegram_bot, mastodon_bot, bsky_bot, telegram_control_bot, nicht_zuordenbar

    Berücksichtigt nur Einträge der letzten <days> Tage.
    """
    cutoff = datetime.now() - timedelta(days=days)

    grouped: dict[str, list[str]] = {k: [] for k in ADMIN_ERROR_GROUPS}
    level_set = {lvl.upper() for lvl in levels}

    if not os.path.exists(log_path):
        grouped["nicht_zuordenbar"].append(f"Logfile nicht gefunden: {log_path}")
        return grouped

    detection_bots = [g for g in ADMIN_ERROR_GROUPS if g != "nicht_zuordenbar"]

    for line in _reverse_read_lines(log_path):
        line_s = line.strip()
        if not line_s:
            continue

        ts, rest = parse_ts_and_rest(line_s)
        if ts is None:
            continue

        if ts < cutoff:
            break

        level, body = split_level_and_body(rest)
        if level not in level_set:
            continue

        bot, _msg = detect_bot_and_message(body, detection_bots)
        key = bot if bot in grouped else "nicht_zuordenbar"

        if len(grouped[key]) < per_group:
            grouped[key].append(_trim_long(line_s))

        # optional: abbrechen, wenn alle Gruppen voll
        if all(len(grouped[k]) >= per_group for k in ADMIN_ERROR_GROUPS):
            break

    return grouped


# ----------------------------
# Commands: /status (alle) + /errors (admin)
# ----------------------------

async def status_command(bot, chat_id: int, admin_view: bool = False):
    """
    /status für alle User:
      Twitter-Modul: <läuft/gestoppt/abgestürzt/...>
      Bluesky-Modul: <läuft/gestoppt/abgestürzt/...>

    Danach: nur Zählwerte (letzte 24h) je Bot:
      - Fehlergruppen (distinct)
      - Auslösungen (Summe)
    """
    include_warnings = admin_view
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

        if admin_view:
            bot_groups = ADMIN_BOT_NAMES_FOR_COUNT
        else:
            # Zeige entweder nur den laufenden Twitter- oder Nitter-Bot,
            # falls genau einer aktiv ist; ansonsten beide.
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
        grouped_errors = count_errors_since_grouped(
            BOT_LOG_FILE,
            bot_groups,
            since,
            levels=("ERROR",)
        )
        grouped_warnings = None
        if admin_view:
            grouped_warnings = count_errors_since_grouped(
                BOT_LOG_FILE,
                bot_groups,
                since,
                levels=("WARNING",)
            )

        lines = []
        lines.append(f"Twitter-Modul: {twitter_state}")
        lines.append(f"Bluesky-Modul: {bsky_state}")
        lines.append(f"Nitter-Modul: {nitter_state}")
        if admin_view:
            lines.append("Admin-Sicht: Fehler und Warnungen getrennt, inkl. Alt-Text, Control-Bots, Gemini Helper.")
        lines.append("")
        lines.append("Fehler (letzte 24 Stunden):")
        lines.append("")

        for botname in bot_groups:
            bot_dict = grouped_errors.get(botname, {}) if grouped_errors else {}
            error_groups = len(bot_dict)
            occurrences = sum(bot_dict.values())
            lines.append(f"- {botname}")
            lines.append(f"  Fehlergruppen: {error_groups}")
            lines.append(f"  Auslösungen:   {occurrences}")
            lines.append("")

        if admin_view and grouped_warnings is not None:
            lines.append("Warnungen (letzte 24 Stunden):")
            lines.append("")
            for botname in bot_groups:
                warn_dict = grouped_warnings.get(botname, {}) if grouped_warnings else {}
                warn_groups = len(warn_dict)
                warn_occurrences = sum(warn_dict.values())
                lines.append(f"- {botname}")
                lines.append(f"  Warngruppen: {warn_groups}")
                lines.append(f"  Auslösungen: {warn_occurrences}")
                lines.append("")

        text = "\n".join(lines).strip()
        for part in split_telegram_text(text):
            await bot.send_message(chat_id=chat_id, text=part)

    except Exception as e:
        logging.error(f"telegram_control_bot: Error in status_command: {e}")
        await bot.send_message(chat_id=chat_id, text="Status konnte nicht ermittelt werden.")


async def admin_errors_command(bot, chat_id: int):
    """
    /errors (oder /error oder /fehler) nur Admin:
    letzte 3 ERROR je Gruppe (letzte 3 Tage) – gut lesbar formatiert
    """
    try:
        grouped = read_last_errors_grouped(BOT_LOG_FILE, per_group=3, days=ADMIN_ERRORS_DAYS)

        lines = []
        lines.append(f"Log: {BOT_LOG_FILE}")
        lines.append(f"Zeitraum: letzte {ADMIN_ERRORS_DAYS} Tage")
        lines.append("Pro Gruppe: max. 3 letzte ERROR")
        lines.append("")

        for module in ADMIN_ERROR_GROUPS:
            errs = grouped.get(module, [])
            lines.append(f"== {module} ==")

            if not errs:
                lines.append("— keine ERRORs —")
                lines.append("")
                continue

            for i, errline in enumerate(errs, start=1):
                lines.append(f"{i}) {errline}")

            lines.append("")

        out = "\n".join(lines).strip()
        for part in split_telegram_text(out):
            await bot.send_message(chat_id=chat_id, text=part)

    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_errors_command: {e}")
        await bot.send_message(chat_id=chat_id, text="Fehlerausgabe konnte nicht gelesen werden.")


async def admin_warnings_command(bot, chat_id: int):
    """
    /warnung (oder /warnungen /warn /warning) nur Admin:
    letzte 3 WARNINGS je Gruppe (letzte 3 Tage) – gut lesbar formatiert
    """
    try:
        grouped = read_last_errors_grouped(
            BOT_LOG_FILE,
            per_group=3,
            days=ADMIN_ERRORS_DAYS,
            levels=("WARNING",)
        )

        lines = []
        lines.append(f"Log: {BOT_LOG_FILE}")
        lines.append(f"Zeitraum: letzte {ADMIN_ERRORS_DAYS} Tage")
        lines.append("Pro Gruppe: max. 3 letzte WARNING")
        lines.append("")

        for module in ADMIN_ERROR_GROUPS:
            warns = grouped.get(module, [])
            lines.append(f"== {module} ==")

            if not warns:
                lines.append("— keine WARNUNGEN —")
                lines.append("")
                continue

            for i, warnline in enumerate(warns, start=1):
                lines.append(f"{i}) {warnline}")

            lines.append("")

        out = "\n".join(lines).strip()
        for part in split_telegram_text(out):
            await bot.send_message(chat_id=chat_id, text=part)

    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_warnings_command: {e}")
        await bot.send_message(chat_id=chat_id, text="Warnungen konnten nicht gelesen werden.")


# ----------------------------
# Persistenz: chat_ids + filter_rules
# ----------------------------

def load_data():
    data = state_store.load_telegram_data()
    if not isinstance(data, dict):
        return {"chat_ids": {}, "filter_rules": {}}
    data.setdefault("chat_ids", {})
    data.setdefault("filter_rules", {})
    return data


def save_data(data):
    payload = data if isinstance(data, dict) else {"chat_ids": {}, "filter_rules": {}}
    payload.setdefault("chat_ids", {})
    payload.setdefault("filter_rules", {})
    try:
        state_store.save_telegram_data(payload)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error saving data: {e}")


def load_chat_ids():
    data = load_data()
    return data["chat_ids"]


def save_chat_ids(chat_ids):
    data = load_data()
    data["chat_ids"] = chat_ids
    save_data(data)


def load_filter_rules(chat_id):
    data = load_data()
    return data["filter_rules"].get(str(chat_id), [])


def save_filter_rules(chat_id, filter_rules):
    data = load_data()
    data["filter_rules"][str(chat_id)] = filter_rules
    save_data(data)


# ----------------------------
# Filter Rules Commands
# ----------------------------

async def add_filter_rules(bot, message, chat_id):
    rules = message.split()[1:]
    try:
        if not rules:
            await add_exempel_command(bot, chat_id)
        else:
            filter_rules = load_filter_rules(chat_id)
            new_rules = set(filter(lambda x: x.strip(), rules))
            filter_rules.extend(new_rules)
            save_filter_rules(chat_id, filter_rules)
            await bot.send_message(chat_id=chat_id, text="Filter rules added.")
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in add_filter_rules: {e}")


async def delete_filter_rules(bot, message, chat_id):
    rules = message.split()[1:]
    try:
        if not rules:
            await del_exempel_command(bot, chat_id)
        else:
            filter_rules = load_filter_rules(chat_id)
            to_remove = set(filter(lambda x: x.strip(), rules))
            filter_rules = [rule for rule in filter_rules if rule not in to_remove]
            save_filter_rules(chat_id, filter_rules)
            await bot.send_message(chat_id=chat_id, text="Filter rules deleted.")
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in delete_filter_rules: {e}")


async def delete_all_rules(bot, message, chat_id):
    save_filter_rules(chat_id, [])
    try:
        await help_command(bot, chat_id)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in delete_all_rules: {e}")


async def show_all_rules(bot, message, chat_id):
    try:
        filter_rules = load_filter_rules(chat_id)
        if filter_rules:
            await bot.send_message(chat_id=chat_id, text="Filter rules:\n" + '\n'.join(filter_rules))
        else:
            await bot.send_message(chat_id=chat_id, text="No filter rules found.")
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in show_all_rules: {e}")


# ----------------------------
# Start/Stop + Hilfe
# ----------------------------

async def start_command(bot, chat_id):
    try:
        chat_ids = load_chat_ids()
        if str(chat_id) not in chat_ids:
            chat_ids[str(chat_id)] = True
            save_chat_ids(chat_ids)
            await bot.send_message(chat_id=chat_id, text="Bot started. Welcome!")
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in start_command: {e}")


async def add_exempel_command(bot, chat_id):
    help_text = "Beispiel für die Funktion zum hinzufügen von Filterstichworten:\n\n"
    help_text += "/addfilterrules [dein Stichwort]\n"
    help_text += "/addfilterrules #S42\n"
    help_text += "/addfilterrules Heerstr\n"
    help_text += "/addfilterrules Alexanderplatz\n"
    help_text += "/addfilterrules #M4_BVG\n"
    help_text += "/addfilterrules #100_BVG\n"
    help_text += "/addfilterrules #U1_BVG\n"
    help_text += "\n"
    help_text += ("In dem Beispiel schickt der Bot dir für die Linien U1, 100 (Bus), M4 (Tram) "
                  "und S42 dir alle Nachichten weiter. Ausserdem für den Alexanderplatz und die Heerstr.\n\n")
    help_text += "Hinweis: Es handelt sich um einen Freitext. Solange der Stichwort in den ankommenden Tweets enthalten ist, kriegst du eine Nachricht."
    try:
        await bot.send_message(chat_id=chat_id, text=help_text)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in add_exempel_command: {e}")


async def del_exempel_command(bot, chat_id):
    help_text = "Beispiel für die Funktion zum löschen von Filterstichworten:\n\n"
    help_text += "/deletefilterrules [dein Stichwort]\n"
    help_text += "/deletefilterrules #S42\n"
    help_text += "/deletefilterrules Heerstr\n"
    help_text += "/deletefilterrules Alexanderplatz\n"
    help_text += "/deletefilterrules #M4_BVG\n"
    help_text += "/deletefilterrules #100_BVG\n"
    help_text += "/deletefilterrules #U1_BVG\n"
    help_text += "\n"
    help_text += ("In dem Beispiel löscht der Bot die Suchbegriffe für die Linien U1, 100 (Bus), M4 (Tram) "
                  "und S42. Ausserdem für den Alexanderplatz und die Heerstr.\n\n")
    help_text += "Das heisst du kriegst für die spezifischen Begriffe keine Nachichten mehr. Achtung achte auf die Schreibweise.\n\n"
    help_text += "Im Zweifelsfall nutze /showallrules um deine bisherigen Filterbegriffe aufzurufen. Sollten keine Begriffe festgelegt sein, bekommst du alle Nachichten weitergeleitet."
    try:
        await bot.send_message(chat_id=chat_id, text=help_text)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in del_exempel_command: {e}")


async def stop_command(bot, chat_id):
    try:
        chat_ids = load_chat_ids()
        if str(chat_id) in chat_ids:
            del chat_ids[str(chat_id)]
            save_chat_ids(chat_ids)
            await bot.send_message(chat_id=chat_id, text="Bot stopped. Goodbye!")
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in stop_command: {e}")


async def help_command(bot, chat_id):
    help_text = "Der Bot leitet alle Tweets von #SbahnBerlin #BVG_Bus #BVG_UBahn #BVG_Tram #VIZ_Berlin an den Nutzer weiter.\n\n"
    help_text += "Außer der Nutzer nutzt die Filterbegriffe-Funktion des Bots. Dann werden nur entsprechende Tweets weitergeleitet.\n\n"
    help_text += (
        "/start - Start the bot\n"
        "/stop - Stop the bot\n"
        "/status - Status Twitter/Bluesky + Fehleranzahl (24h)\n"
        "/addfilterrules [rules] - Add filter rules\n"
        "/deletefilterrules [rules] - Delete filter rules\n"
        "/deleteallrules - Delete all filter rules\n"
        "/showallrules - Show all filter rules\n"
        "/list - bietet eine ausführliche Anleitung für Filter hinzufügen/löschen\n"
        "/about - Infos zum Bot\n"
        "/datenschutz (/privacy /data) - Welche Daten gespeichert werden\n"
    )
    try:
        await bot.send_message(chat_id=chat_id, text=help_text)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in help_command1: {e}")

    expert_text = "Expertentipps:\n\n"
    expert_text += "Dem Bot können mehrere Stichworte gleichzeitig übergeben werden (getrennt durch Leerzeichen)\n\n"
    expert_text += "Kurzformen:\n"
    expert_text += "/addrule [stichworte]\n"
    expert_text += "/delrule [stichworte]\n"
    try:
        await bot.send_message(chat_id=chat_id, text=expert_text)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in help_command2: {e}")


async def admin_help(bot, chat_id):
    help_text = "/send Dein Text wird an alle Nutzer des Bots einschließlich Mastodon und Telegram gesendet\n\n"
    help_text += "/me Dein Text an dich selber gesendet, zum testen\n\n"
    help_text += "/telegram Dein Text wird an alle Telegram Bot Nutzer gesendet\n\n"
    help_text += "/mastodon Dein Text wird an alle Mastodon Bot Nutzer gesendet\n\n"
    help_text += f"/errors (oder /error oder /fehler) zeigt die letzten 3 ERROR je Gruppe (letzte {ADMIN_ERRORS_DAYS} Tage)\n"
    help_text += f"/warnung (oder /warnungen /warn /warning) zeigt die letzten 3 WARNINGS je Gruppe (letzte {ADMIN_ERRORS_DAYS} Tage)\n"
    help_text += "/archivelogs verschiebt das aktuelle Log in die DB (Archiv 90d, Live 7d)"
    try:
        await bot.send_message(chat_id=chat_id, text=help_text)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_help: {e}")


def about_text() -> str:
    return (
        "Über den öpnv_berlin_bot\n\n"
        "Ich sammle ÖPNV-Meldungen (BVG/S-Bahn, VIZ, Bahn-Störung) von Twitter/X (über Nitter), Bluesky und ähnlichen Quellen und schicke sie an Mastodon – auf Wunsch auch per Telegram direkt an dich.\n"
        "Mastodon läuft automatisch; Filter und Admin-Funktionen hier sind optional.\n"
        "Alles läuft lokal auf meinem Server, keine Cloud-Dienste. Quellcode & Feedback: GitHub https://github.com/Sam4000der2/selenium_twitter_Webcrawler_de"
    )


def privacy_text() -> str:
    return (
        "Der Telegram-Teil dieses Bots leitet ausschließlich öffentlich zugängliche Meldungen von Twitter/X und Bluesky (u.a. S-Bahn, Deutsche Bahn, Polizei, Feuerwehr, VIZ Berlin sowie ggf. weitere öffentliche Accounts) in Telegram weiter. Bilder werden nicht übertragen; es findet keine Alt-Text-Erstellung (Gemini) statt.\n\n"
        "Am Ende jeder Telegram-Nachricht wird ein Link zum Originalbeitrag mitgesendet: bei Twitter/X über nitter.net, bei Bluesky direkt. Beim Öffnen dieser Links gelten die Datenschutzbestimmungen der jeweiligen Anbieter.\n\n"
        "Für die Zustellung in Telegram speichere ich lokal die Chat-ID sowie die vom Nutzer gesetzten Einstellungen (Filterbegriffe, Bot an/aus). Weitere Daten werden nicht erhoben.\n\n"
        "Löschung/Stop: /stop beendet den Bot, /deleteallrules entfernt deine Filterbegriffe.\n\n"
        "Kontakt/Löschung: https://troet.cafe/@sam4000"
    )


# ----------------------------
# Admin Send-Funktionen
# ----------------------------

def service_tweet(message):
    service_message = []
    zeitstempel = datetime.now().strftime("%d.%m.%Y %H:%M")
    service_message.append({
        "user": "Servicemeldung",
        "username": "Servicemeldung",
        "content": message,
        "posted_time": zeitstempel,
        "var_href": "",
        "images": "",
        "extern_urls": "",
        "images_as_string": "",
        "extern_urls_as_string": ""
    })
    return service_message


def text_formatierer(message):
    message = message.replace('. ', '.\n')
    message = message.replace(': ', ':\n')
    message = message.replace('! ', '!\n')
    message = message.replace('? ', '?\n')
    message = message.replace('/n', '\n')
    return message


def build_service_messages(formated_message: str) -> tuple[list[dict], bool]:
    """
    Gibt die Service-Nachrichten als Liste zurück und markiert, ob ein Thread gebaut werden soll.
    """
    parts = split_mastodon_text(formated_message)
    threaded = len(parts) > 1
    return service_tweet(formated_message), threaded


async def admin_telegram_send(message):
    formated_message = text_formatierer(message)
    service_message = service_tweet(formated_message)
    try:
        await telegram_bot.main(service_message)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_telegram_send: {e}")


async def admin_mastodon_send(message):
    formated_message = text_formatierer(message)
    try:
        service_messages, threaded = build_service_messages(formated_message)
        await mastodon_bot.main(service_messages, thread=threaded)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_mastodon_send: {e}")


async def admin_send_all(message):
    formated_message = text_formatierer(message)
    service_message = service_tweet(formated_message)

    try:
        await telegram_bot.main(service_message)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_send_all - telegram: {e}")

    try:
        service_messages, threaded = build_service_messages(formated_message)
        await mastodon_bot.main(service_messages, thread=threaded)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_send_all - mastodon: {e}")


async def admin_send_me(message):
    message_content = text_formatierer(message)
    service_message = []
    zeitstempel = datetime.now().strftime("%d.%m.%Y %H:%M")
    service_message.append({
        "user": "me_1234_me",
        "username": "me_1234_me",
        "content": message_content,
        "posted_time": zeitstempel,
        "var_href": "",
        "images": "",
        "extern_urls": "",
        "images_as_string": "",
        "extern_urls_as_string": ""
    })
    try:
        await telegram_bot.main(service_message)
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_send_me: {e}")


async def admin_archive_logs(bot, chat_id: int):
    """
    Verschiebt alle Zeilen aus dem zentralen Log in die DB:
    - Archiv-Bucket mit 90 Tagen Retention
    - Live-Bucket mit 7 Tagen Retention
    """
    try:
        if not os.path.exists(BOT_LOG_FILE):
            await bot.send_message(chat_id=chat_id, text=f"Logdatei fehlt: {BOT_LOG_FILE}")
            return

        entries: list[dict] = []
        with open(BOT_LOG_FILE, "r", encoding="utf-8") as f:
            for raw in f:
                line = (raw or "").strip()
                if not line:
                    continue
                ts, _rest = parse_ts_and_rest(line)
                if ts is None:
                    continue
                entries.append({"ts": int(ts.timestamp()), "line": line})

        archived = state_store.store_archive_logs(entries)
        live = state_store.store_live_logs(entries)
        state_store.prune_logs()

        msg_lines = [
            f"Log-Archivierung abgeschlossen.",
            f"Verarbeitete Zeilen: {len(entries)}",
            f"Archiv (90 Tage): {len(archived)} Einträge",
            f"Live (7 Tage): {len(live)} Einträge",
        ]
        await bot.send_message(chat_id=chat_id, text="\n".join(msg_lines))
    except Exception as e:
        logging.error(f"telegram_control_bot: Error in admin_archive_logs: {e}")
        await bot.send_message(chat_id=chat_id, text="Archivierung ist fehlgeschlagen.")


# ----------------------------
# Bot Loop
# ----------------------------

async def start_bot():
    if not BOT_TOKEN:
        logging.error("telegram_control_bot: Start abgebrochen – ENV 'telegram_token' fehlt.")
        return

    bot = telegram.Bot(token=BOT_TOKEN)
    update_id = None
    backoff_seconds = 1

    while True:
        try:
            updates = await bot.get_updates(offset=update_id)
            backoff_seconds = 1  # Reset nach erfolgreichem Abruf
        except Exception as e:
            logging.error(f"telegram_control_bot: Error getting updates in start_bot: {e}")
            await asyncio.sleep(backoff_seconds)
            backoff_seconds = min(backoff_seconds * 2, 300)
            continue

        for update in updates:
            try:
                update_id = update.update_id + 1
                await process_update(bot, update)
            except Exception as e:
                logging.error(f"telegram_control_bot: Error in start_bot - for Schleife: {e}")


async def process_update(bot, update):
    try:
        if update.message:
            message = (update.message.text or "").strip()
            chat_id = update.message.chat.id
            lower = message.lower()

            if lower.startswith('/start'):
                await start_command(bot, chat_id)
                await help_command(bot, chat_id)

            elif lower.startswith('/stop'):
                await stop_command(bot, chat_id)

            elif lower.startswith('/hilfe'):
                await help_command(bot, chat_id)

            elif lower.startswith('/status'):
                await status_command(bot, chat_id, admin_view=(admin is not None and chat_id == admin))

            elif lower.startswith('/about'):
                await bot.send_message(chat_id=chat_id, text=about_text())

            elif lower.startswith('/datenschutz') or lower.startswith('/privacy') or lower.startswith('/data'):
                await bot.send_message(chat_id=chat_id, text=privacy_text())

            elif lower.startswith('/add'):
                await add_filter_rules(bot, message, chat_id)

            elif lower.startswith('/deleteallrules'):
                await delete_all_rules(bot, message, chat_id)

            elif lower.startswith('/del'):
                await delete_filter_rules(bot, message, chat_id)

            elif lower.startswith('/showallrules'):
                await show_all_rules(bot, message, chat_id)

            elif lower.startswith('/list'):
                await add_exempel_command(bot, chat_id)
                await del_exempel_command(bot, chat_id)

            elif lower.startswith('/') and (admin is not None) and (chat_id == admin):
                command, *args = lower.split()
                message_content = ' '.join(args)

                if command in ('/errors', '/error', '/fehler'):
                    await admin_errors_command(bot, chat_id)
                elif command in ('/warnung', '/warnungen', '/warn', '/warning'):
                    await admin_warnings_command(bot, chat_id)
                elif command == '/me' and message_content:
                    await admin_send_me(message_content)
                elif command.startswith('/mast') and message_content:
                    await admin_mastodon_send(message_content)
                elif command.startswith('/tele') and message_content:
                    await admin_telegram_send(message_content)
                elif command == '/send' and message_content:
                    await admin_send_all(message_content)
                elif command in ('/archivelogs', '/logarchive', '/logsarchive'):
                    await admin_archive_logs(bot, chat_id)
                elif command in ('/help', '/hilfe'):
                    await help_command(bot, chat_id)
                    await admin_help(bot, chat_id)
                else:
                    await admin_help(bot, chat_id)

            elif lower.startswith('/'):
                await help_command(bot, chat_id)

            else:
                await start_command(bot, chat_id)
                await help_command(bot, chat_id)

    except Exception as e:
        logging.error(f"telegram_control_bot: Error in process_update: {e}")


if __name__ == "__main__":
    asyncio.run(start_bot())
