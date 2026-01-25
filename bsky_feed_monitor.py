#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import argparse
import logging
import asyncio
import hashlib
import re

import feedparser
from dateutil.parser import parse
import pytz

# Falls du Telegram/Mastodon-Module hast, wie im Original
# die erwartet async main(new_tweets) aufzurufen.
import telegram_bot
import mastodon_bot
import state_store

# -------------------------
# Logging configuration
# -------------------------
# Ursprünglich war level=logging.ERROR; fürs Debug/Info beim Start setze ich INFO.
# Wenn du nur Fehler möchtest, ändere auf logging.ERROR.
logging.basicConfig(
    filename='/home/sascha/bots/twitter_bot.log',
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s'
)

# -------------------------
# Konfiguration der Feeds
# -------------------------
FEEDS = [
    {
        "name": "VIZ Berlin",
        "url": "https://bsky.app/profile/vizberlin.bsky.social/rss",
        "storage_key": "viz_berlin"
        # Optional: "max_entries": 200
    }
    # Hier weitere Feeds hinzufügen
]

# Grundeinstellungen zur History-Größe (kann in FEEDS per "max_entries" pro Feed überschrieben werden)
MIN_KEEP = 20        # mindestens so viele IDs behalten (falls Feed sehr klein ist)
MAX_KEEP_CAP = 1000  # absolute Obergrenze
MAX_ENTRY_AGE_SECONDS = 3 * 60 * 60  # nur Einträge der letzten 3 Stunden berücksichtigen

PLACEHOLDER_MARKERS = [
    "[contains quote post or other embedded content]"
]

# -------------------------
# Hilfsfunktionen: Lesen/Schreiben aus der gemeinsamen DB
# -------------------------
def load_saved_ids(feed_key: str):
    """
    Liest gespeicherte URLs aus der DB.
    Liefert Liste (Strings), oder [] wenn Datei fehlt/leer/Fehler.
    """
    entries = state_store.load_bsky_entries(feed_key)
    if entries:
        logging.info(f"Loaded {len(entries)} saved links from DB key {feed_key}")
    else:
        logging.info(f"Keine gespeicherten Links für DB key {feed_key}, starte leer.")
    return entries


def save_ids(ids_list, feed_key: str):
    """
    Speichert eine Liste von URLs in der DB.
    """
    try:
        state_store.save_bsky_entries(feed_key, ids_list)
        logging.info(f"Saved {len(ids_list)} links to DB key {feed_key}")
    except Exception as e:
        logging.error(f"bsky_bot: Fehler beim Schreiben der Einträge für {feed_key}: {e}")

# -------------------------
# ID/Time helpers
# -------------------------
def _make_canonical_id_from_parsed(item):
    """
    Verwende link als Primärschlüssel; fallback auf id/guid, title+published oder SHA256.
    Gibt einen getrimmten String zurück.
    """
    link = item.get("link")
    if link:
        return str(link).strip()
    for key in ("id", "guid"):
        val = item.get(key)
        if val:
            return str(val).strip()
    title = item.get("title", "") or ""
    published = item.get("published", "") or item.get("updated", "") or ""
    if title or published:
        return f"{title}|{published}"
    fallback = (item.get("title", "") or "") + (item.get("summary", "") or "") + (item.get("link", "") or "")
    return hashlib.sha256(fallback.encode("utf-8")).hexdigest()

def _get_parsed_time(item):
    """
    Gibt epoch (float) zurück, oder None.
    Benutzt feedparser's published_parsed/updated_parsed oder versucht parse() auf string.
    """
    t = item.get("published_parsed") or item.get("updated_parsed")
    if t:
        try:
            return time.mktime(t)
        except Exception:
            return None
    pub = item.get("published") or item.get("updated")
    if pub:
        try:
            dt = parse(pub)
            return dt.timestamp()
        except Exception:
            return None
    return None


def clean_description(desc: str) -> str:
    """
    Entfernt bekannte Platzhalter-Texte und normalisiert Zeilenumbrüche.
    """
    text = (desc or "").replace("\r\n", "\n")
    for marker in PLACEHOLDER_MARKERS:
        text = text.replace(marker, "")
    # Doppelte Leerzeilen auf maximal zwei begrenzen
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# -------------------------
# Feed Parsing: Link-basiert + Startup 30min logic
# -------------------------
def parse_feed(feed_config, debug: bool = False):
    """
    Parst einen Feed. Vergleicht ausschließlich über link.
    Beim Start: falls keine gespeicherten Links vorhanden sind, werden
    alle Einträge, die älter als 3 Stunden sind, als gelesen markiert und gespeichert.
    Rückgabe: Liste neuer Einträge (dict)
    """
    feed_name = feed_config["name"]
    feed_url = feed_config["url"]
    feed_key = feed_config.get("storage_key") or feed_name

    try:
        now = time.time()
        logging.info(f"Überprüfe Feed: {feed_name} ({feed_url})")
        feed = feedparser.parse(feed_url)

        if getattr(feed, "bozo", False) and hasattr(feed, "bozo_exception"):
            logging.debug(f"feedparser bozo for {feed_name}: {feed.bozo_exception}")

        entries = list(feed.entries or [])
        # Sortiere nach published/updated (neueste zuerst), falls möglich
        try:
            entries_sorted = sorted(entries, key=lambda x: (_get_parsed_time(x) or 0), reverse=True)
        except Exception:
            entries_sorted = entries

        # gewünschte Anzahl zu behalten: optional per-feed override oder dynamisch anhand Feedgröße
        desired_keep = feed_config.get("max_entries")
        if desired_keep is None:
            desired_keep = min(max(len(entries_sorted), MIN_KEEP), MAX_KEEP_CAP)

        # Lade gespeicherte Links (eine pro Zeile)
        saved_ids = load_saved_ids(feed_key)

        # Wenn beim Start keine gespeicherten Links vorhanden sind: markiere ältere Einträge (3h) als gelesen
        if not saved_ids:
            cutoff = MAX_ENTRY_AGE_SECONDS
            initial_saved = []
            for item in entries_sorted:
                ts = _get_parsed_time(item)
                if ts and (now - ts) > cutoff:
                    lid = _make_canonical_id_from_parsed(item)
                    if lid and lid not in initial_saved:
                        initial_saved.append(lid)
            if initial_saved:
                # speichere die als gelesen markierten Links (eine pro Zeile)
                if not debug:
                    save_ids(initial_saved, feed_key)
                saved_ids = initial_saved
                logging.info(f"Startup: {len(initial_saved)} alte Einträge (>3h) als gelesen markiert for {feed_name}")
            else:
                logging.info(f"Startup: keine alten Einträge (>3h) zum Markieren für {feed_name}")

        saved_set = set(saved_ids)
        new_entries = []
        all_ids = []

        for item in entries_sorted:
            item_id = _make_canonical_id_from_parsed(item)
            all_ids.append(item_id)

            ts = _get_parsed_time(item)
            if ts and (now - ts) > MAX_ENTRY_AGE_SECONDS:
                logging.debug(f"Überspringe alten Eintrag (>3h) für {feed_name}: {item_id}")
                continue

            if item_id not in saved_set:
                entry = {
                    "id": item_id,
                    "guid": item.get("guid") or item.get("id") or "",
                    "link": item.get("link", ""),
                    "title": item.get("title", ""),
                    "description": clean_description(item.get("description", "") or item.get("summary", "")),
                    "pubDate": item.get("published") or item.get("updated") or "",
                    "feed_name": feed_name
                }
                new_entries.append(entry)
                saved_set.add(item_id)

        # Entferne Duplikate in all_ids, dabei Reihenfolge beibehalten (neueste zuerst)
        seen = set()
        unique_all_ids = []
        for i in all_ids:
            if i and i not in seen:
                seen.add(i)
                unique_all_ids.append(i)

        # Schreibe die History (neueste oben), auf desired_keep trimmen
        if not debug:
            save_ids(unique_all_ids[:desired_keep], feed_key)
        logging.info(f"Feed {feed_name}: {len(new_entries)} neue Einträge, history-size={len(unique_all_ids[:desired_keep])}")

        return new_entries

    except Exception as e:
        logging.error(f"bsky_bot: Fehler beim Parsen des Feeds {feed_name}: {e}")
        return []

# -------------------------
# Formatierung / Ausgabe
# -------------------------
def format_post_date(timestamp):
    """
    Konvertiert den Zeitstempel in das Format "DD.MM.YYYY HH:MM".
    Wenn parse fehlschlägt, wird der Original-String zurückgegeben.
    """
    if not timestamp:
        return ""
    try:
        dt = parse(timestamp)
        if dt.tzinfo is None:
            # Annahme: UTC, falls keine TZ vorhanden
            dt = dt.replace(tzinfo=pytz.utc)
        local = dt.astimezone(pytz.timezone('Europe/Berlin'))
        return local.strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(timestamp)

def format_entry(entry):
    """Formatiert einen Eintrag für die Ausgabe."""
    pub = entry.get('pubDate', '')
    description = clean_description(entry.get('description', ''))
    try:
        posted_time = format_post_date(pub) if pub else pub
    except Exception:
        posted_time = pub or ""
    return f"""
Neuer Eintrag von {entry.get('feed_name', 'Unbekannt')}:
{description}

Veröffentlicht am: {posted_time}
Link: {entry.get('link', '')}
"""

# -------------------------
# Hauptlogik: check_all_feeds + main loop
# -------------------------
def check_all_feeds(debug: bool = False):
    """Prüft alle konfigurierten Feeds auf neue Einträge."""
    total_new_entries = 0
    tweet_data = []

    for feed_config in FEEDS:
        new_entries = parse_feed(feed_config, debug=debug)

        if new_entries:
            total_new_entries += len(new_entries)
            logging.info(f"- {len(new_entries)} neue Einträge für {feed_config['name']} gefunden")

            for entry in new_entries:
                formatted_entry = format_entry(entry)
                user = entry["feed_name"].replace(' ', '_')
                description = clean_description(entry.get("description", ""))
                posted_time = format_post_date(entry.get("pubDate", ""))  # Zeitformatierung vornehmen
                tweet_data.append({
                    "feed_name": feed_config["name"],
                    "user": user,
                    "username": user,
                    "content": description,
                    "posted_time": posted_time,
                    "var_href": entry.get("link", ""),
                    "images": None,
                    "extern_urls": None,
                    "images_as_string": None,
                    "extern_urls_as_string": ""
                })
        else:
            logging.debug(f"- Keine neuen Einträge für {feed_config['name']}")

    return tweet_data if tweet_data else None

async def main():
    while True:
        new_tweets = check_all_feeds()
        if new_tweets:
            try:
                await telegram_bot.main(new_tweets)
            except Exception as e:
                logging.error(f"bsky_bot: An error occurred in telegram_bot: {e}")

            try:
                await mastodon_bot.main(new_tweets)
            except Exception as e:
                logging.error(f"bsky_bot: An error occurred in mastodon_bot: {e}")
        await asyncio.sleep(60)

def run_debug():
    logging.info("Debug-Modus: einmaliger Lauf, keine Zustellung oder DB-Updates.")
    new_tweets = check_all_feeds(debug=True)
    if not new_tweets:
        print("Debug: keine neuen Einträge (<=3h) gefunden; gespeicherte IDs wurden berücksichtigt.")
        return

    print(f"Debug: {len(new_tweets)} neue Einträge (keine Zustellung, gespeicherte IDs berücksichtigt):")
    for entry in new_tweets:
        feed = entry.get("feed_name") or entry.get("user") or "Unbekannt"
        posted = entry.get("posted_time") or ""
        link = entry.get("var_href") or ""
        snippet = (entry.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        print(f"- {feed} | {posted} | {link}")
        if snippet:
            print(f"  Text: {snippet}")


def run_no_sending():
    logging.info("No-send-Modus: einmaliger Lauf, DB/History wird aktualisiert, keine Zustellung.")
    new_tweets = check_all_feeds(debug=False)
    if not new_tweets:
        print("No-send: keine neuen Einträge (<=3h) gefunden; DB wurde ggf. aktualisiert.")
        return

    print(f"No-send: {len(new_tweets)} neue Einträge (DB aktualisiert, keine Zustellung):")
    for entry in new_tweets:
        feed = entry.get("feed_name") or entry.get("user") or "Unbekannt"
        posted = entry.get("posted_time") or ""
        link = entry.get("var_href") or ""
        snippet = (entry.get("content") or "").strip().replace("\n", " ")
        if len(snippet) > 160:
            snippet = snippet[:157] + "..."
        print(f"- {feed} | {posted} | {link}")
        if snippet:
            print(f"  Text: {snippet}")


def _parse_args():
    parser = argparse.ArgumentParser(description="Bluesky Feed Monitor")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Einmaliger Lauf ohne Zustellung; nutzt gespeicherte Einträge, schreibt aber keine neuen."
    )
    parser.add_argument(
        "--nosending", "--no-send", "--dry-run",
        dest="no_send",
        action="store_true",
        help="Einmaliger Lauf ohne Zustellung; DB/History wird aktualisiert."
    )
    args = parser.parse_args()
    if args.debug and args.no_send:
        parser.error("--debug und --no-send können nicht gemeinsam verwendet werden.")
    return args


def _run():
    args = _parse_args()
    if args.debug:
        run_debug()
        return
    if args.no_send:
        run_no_sending()
        return

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot beendet (KeyboardInterrupt).")
    except Exception as e:
        logging.error(f"bsky_bot: Uncaught exception in main: {e}")


if __name__ == '__main__':
    _run()
