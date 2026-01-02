#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import asyncio
import hashlib
import tempfile
import re

import feedparser
from dateutil.parser import parse
import pytz

# Falls du Telegram/Mastodon-Module hast, wie im Original
# die erwartet async main(new_tweets) aufzurufen.
import telegram_bot
import mastodon_bot

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
        "file": "/home/sascha/bots/viz_berlin_entries.txt"
        # Optional: "max_entries": 200
    }
    # Hier weitere Feeds hinzufügen
]

# Grundeinstellungen zur History-Größe (kann in FEEDS per "max_entries" pro Feed überschrieben werden)
MIN_KEEP = 20        # mindestens so viele IDs behalten (falls Feed sehr klein ist)
MAX_KEEP_CAP = 1000  # absolute Obergrenze

PLACEHOLDER_MARKERS = [
    "[contains quote post or other embedded content]"
]

# -------------------------
# Hilfsfunktionen: Lesen/Schreiben (eine URL pro Zeile)
# -------------------------
def load_saved_ids(file_path):
    """
    Liest gespeicherte URLs (eine URL pro Zeile).
    Liefert Liste (Strings), oder [] wenn Datei fehlt/leer/Fehler.
    """
    if not os.path.exists(file_path):
        logging.info(f"Datei {file_path} nicht gefunden. Starte mit leerer Liste.")
        return []
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip()]
        logging.info(f"Loaded {len(lines)} saved links from {file_path}")
        return lines
    except Exception as e:
        logging.error(f"bsky_bot: Fehler beim Lesen der Datei {file_path}: {e}")
        return []

def save_ids(ids_list, file_path):
    """
    Speichert eine Liste von URLs, je eine pro Zeile. Atomar (tmp -> replace).
    """
    try:
        dirpath = os.path.dirname(file_path) or "."
        os.makedirs(dirpath, exist_ok=True)
        # write to a temp file in the same directory, then atomically replace
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=dirpath, prefix=".tmp_") as tmpf:
            for item in ids_list:
                if item:
                    tmpf.write(str(item).strip() + "\n")
            tmpname = tmpf.name
            tmpf.flush()
            os.fsync(tmpf.fileno())
        os.replace(tmpname, file_path)
        logging.info(f"Saved {len(ids_list)} links to {file_path}")
    except Exception as e:
        logging.error(f"bsky_bot: Fehler beim Schreiben der Datei {file_path}: {e}")

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
def parse_feed(feed_config):
    """
    Parst einen Feed. Vergleicht ausschließlich über link.
    Beim Start: falls keine gespeicherten Links vorhanden sind, werden
    alle Einträge, die älter als 30 Minuten sind, als gelesen markiert und gespeichert.
    Rückgabe: Liste neuer Einträge (dict)
    """
    feed_name = feed_config["name"]
    feed_url = feed_config["url"]
    entries_file = feed_config["file"]

    try:
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
        saved_ids = load_saved_ids(entries_file)

        # Wenn beim Start keine gespeicherten Links vorhanden sind: markiere ältere Einträge (30min) als gelesen
        if not saved_ids:
            now = time.time()
            cutoff = 30 * 60  # 30 Minuten in Sekunden
            initial_saved = []
            for item in entries_sorted:
                ts = _get_parsed_time(item)
                if ts and (now - ts) > cutoff:
                    lid = _make_canonical_id_from_parsed(item)
                    if lid and lid not in initial_saved:
                        initial_saved.append(lid)
            if initial_saved:
                # speichere die als gelesen markierten Links (eine pro Zeile)
                save_ids(initial_saved, entries_file)
                saved_ids = initial_saved
                logging.info(f"Startup: {len(initial_saved)} alte Einträge (>30min) als gelesen markiert for {feed_name}")
            else:
                logging.info(f"Startup: keine alten Einträge (>30min) zum Markieren für {feed_name}")

        saved_set = set(saved_ids)
        new_entries = []
        all_ids = []

        for item in entries_sorted:
            item_id = _make_canonical_id_from_parsed(item)
            all_ids.append(item_id)

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

        # Entferne Duplikate in all_ids, dabei Reihenfolge beibehalten (neueste zuerst)
        seen = set()
        unique_all_ids = []
        for i in all_ids:
            if i and i not in seen:
                seen.add(i)
                unique_all_ids.append(i)

        # Schreibe die History (neueste oben), auf desired_keep trimmen
        save_ids(unique_all_ids[:desired_keep], entries_file)
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
def check_all_feeds():
    """Prüft alle konfigurierten Feeds auf neue Einträge."""
    total_new_entries = 0
    tweet_data = []

    for feed_config in FEEDS:
        new_entries = parse_feed(feed_config)

        if new_entries:
            total_new_entries += len(new_entries)
            logging.info(f"- {len(new_entries)} neue Einträge für {feed_config['name']} gefunden")

            for entry in new_entries:
                formatted_entry = format_entry(entry)
                user = entry["feed_name"].replace(' ', '_')
                description = clean_description(entry.get("description", ""))
                posted_time = format_post_date(entry.get("pubDate", ""))  # Zeitformatierung vornehmen
                tweet_data.append({
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

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Bot beendet (KeyboardInterrupt).")
    except Exception as e:
        logging.error(f"bsky_bot: Uncaught exception in main: {e}")
