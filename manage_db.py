#!/usr/bin/env python3
"""
Einfaches Verwaltungsskript für die gemeinsame Datenbank (nitter_bot.db).
Ohne Fachchinesisch: Du kannst Tabelleninhalte ansehen, Werte setzen oder löschen.

Start: python3 manage_db.py [--db-path /pfad/zur/db]
Ohne Angabe nimmt das Skript seinen eigenen Ordner und legt dort nitter_bot.db an.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any
from datetime import datetime

import storage
import state_store

BUCKETS = {
    "telegram": ("telegram_config", "chat_config"),
    "mastodon_regeln": ("mastodon_rules", "rules"),
    "mastodon_versionen": ("mastodon_versions", "versions"),
    "gemini_modelle": ("gemini_models", "cache"),
    "twitter_verlauf": ("twitter_history", "existing_tweets"),
    "nitter_verlauf": ("nitter_history", None),
    "nitter_nutzer": ("nitter_users", None),
    "bsky_feeds": ("bsky_feed_history", None),
    "logs_live": ("logs_live", None),
    "logs_archiv": ("logs_archive", None),
}


def pretty(obj: Any) -> str:
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2)
    except Exception:
        return str(obj)


def prompt(text: str) -> str:
    try:
        return input(text)
    except EOFError:
        return ""


def show_menu():
    print("\n--- Datenbank verwalten ---")
    print("1) Alle Tabellen anzeigen")
    print("2) Inhalt einer Tabelle anzeigen")
    print("3) Einzelnen Eintrag anzeigen")
    print("4) Eintrag speichern/ändern")
    print("5) Eintrag löschen")
    print("6) Liste an Eintrag anhängen")
    print("7) Twitter/Nitter Nutzer verwalten")
    print("8) Schnelles Löschen von Tweets/Posts (URL)")
    print("0) Beenden")


def choose_bucket() -> tuple[str, str | None]:
    print("\nVerfügbare Tabellen:")
    for name, (bucket, default_key) in BUCKETS.items():
        key_info = f" (default key: {default_key})" if default_key else ""
        print(f"- {name}: {bucket}{key_info}")
    chosen = prompt("Tabellenname (oder direkter Tabellenwert) eingeben: ").strip()
    if not chosen:
        return "", None
    if chosen in BUCKETS:
        return BUCKETS[chosen]
    return chosen, None


def load_bucket(bucket: str):
    return storage.get_bucket(bucket)


def read_value(bucket: str, key: str):
    return storage.read_value(bucket, key, None)


def set_value(bucket: str, key: str, raw_json: str):
    try:
        value = json.loads(raw_json)
    except Exception:
        value = raw_json
    storage.write_value(bucket, key, value)


def delete_key(bucket: str, key: str):
    storage.delete_entry(bucket, key)


def append_list(bucket: str, key: str, raw_json: str):
    try:
        items = json.loads(raw_json)
        if not isinstance(items, list):
            items = [items]
    except Exception:
        items = [raw_json]
    storage.append_to_list(bucket, key, items)


def manage_nitter_users():
    users = state_store.load_nitter_users()
    if not isinstance(users, dict):
        users = {}

    while True:
        names = ", ".join(sorted(users.keys())) if users else "(keine Einträge)"
        print("\n--- Twitter/Nitter Nutzer ---")
        print(f"Aktuell: {names}")
        print("a) Nutzer hinzufügen/ändern")
        print("l) Nutzer löschen")
        print("s) Nur anzeigen")
        print("q) Zurück")
        choice = prompt("Auswahl: ").strip().lower() or "s"

        if choice == "q":
            break
        elif choice == "s":
            print(pretty(users))
        elif choice == "a":
            username = prompt("Nutzername (z.B. SBahnBerlin): ").strip()
            if not username:
                print("Kein Nutzername angegeben.")
                continue
            interval_raw = prompt("Intervall in Sekunden (leer=900): ").strip()
            try:
                interval = int(interval_raw) if interval_raw else 900
            except Exception:
                interval = 900
            active_start = prompt("Aktiv ab (HH:MM, leer=immer): ").strip()
            active_end = prompt("Aktiv bis (HH:MM, leer=immer): ").strip()
            users[username] = {
                "interval_seconds": interval,
                "active_start": active_start,
                "active_end": active_end,
            }
            state_store.save_nitter_users(users)
            print(f"{username} gespeichert.")
        elif choice == "l":
            username = prompt("Welcher Nutzer soll gelöscht werden? ").strip()
            if not username:
                print("Kein Nutzername angegeben.")
                continue
            if username not in users:
                print("Nutzer nicht gefunden.")
                continue
            confirm = prompt(f"{username} wirklich löschen? (ja/nein): ").strip().lower()
            if confirm in {"ja", "y", "yes"}:
                users.pop(username, None)
                state_store.save_nitter_users(users)
                print("Gelöscht.")
            else:
                print("Abgebrochen.")
        else:
            print("Unbekannte Auswahl.")


def quick_delete_urls():
    """
    URLs schnell löschen: Twitter-Verlauf, Nitter-Verlauf (optional nach Nutzer),
    Bluesky-Feeds. Fragt je nach Ziel nach Filter (alle/zeitraum/nutzer).
    """
    mode = prompt("Schnell löschen per URL (u) oder Dialog (d)? [u/d]: ").strip().lower() or "u"
    if mode == "u":
        urls_raw = prompt("URL(s) kommasepariert eingeben: ").strip()
        urls = [u.strip() for u in urls_raw.split(",") if u.strip()]
        if not urls:
            print("Keine URLs angegeben.")
            return

        x_urls = []
        bsky_urls = []
        def normalize_x(url: str) -> str:
            u = url.strip()
            lower = u.lower().replace("http://", "").replace("https://", "")
            # Known aliases -> x.com
            aliases = [
                ("nitter.net", "x.com"),
                ("twitter.com", "x.com"),
                ("www.twitter.com", "x.com"),
                ("mobile.twitter.com", "x.com"),
                ("x.com", "x.com"),
                ("localhost:8081", "x.com"),
                ("192.168.178.26:8081", "x.com"),
            ]
            for old, new in aliases:
                if old in lower:
                    normalized = lower.replace(old, new)
                    if not normalized.startswith("https://"):
                        normalized = "https://" + normalized.lstrip("/")
                    return normalized
            normalized = lower
            if not normalized.startswith("https://"):
                normalized = "https://" + normalized.lstrip("/")
            return normalized

        for url in urls:
            low = url.lower()
            if "bsky.app" in low or "bluesky" in low:
                bsky_urls.append(url)
            else:
                x_urls.append(normalize_x(url))

        # Twitter
        t_entries = state_store.get_twitter_history_entries()
        t_remaining = [e for e in t_entries if normalize_x(e["url"]) not in x_urls]
        state_store.save_twitter_history([e["url"] for e in t_remaining])

        # Nitter (alle Nutzer)
        n_entries = state_store.get_nitter_history_entries()
        n_remaining = [e for e in n_entries if normalize_x(e["url"]) not in x_urls]
        n_map: dict[str, list[str]] = {}
        for e in n_remaining:
            n_map.setdefault(e["username"], []).append(e["url"])
        state_store.save_nitter_history(n_map)

        # Bluesky (alle Feeds)
        if bsky_urls:
            feed_names = state_store.get_bsky_feed_names()
            total_removed = 0
            for feed in feed_names:
                entries = state_store.load_bsky_entries(feed)
                remaining = [u for u in entries if u not in bsky_urls]
                total_removed += len(entries) - len(remaining)
                state_store.save_bsky_entries(feed, remaining)
            print(f"Bluesky: {total_removed} Einträge entfernt.")

        deleted = (len(t_entries) - len(t_remaining)) + (len(n_entries) - len(n_remaining))
        print(f"Twitter/Nitter: {deleted} Einträge entfernt.")
    else:
        target = prompt("Was soll bearbeitet werden? (t=Twitter, n=Nitter, b=Bluesky): ").strip().lower()
        if target == "t":
            entries = state_store.get_twitter_history_entries()
            print(f"{len(entries)} Einträge gefunden.")
            scope = prompt("Alle (a), nach Stichwort (s), Zeitraum (z)? [a/s/z]: ").strip().lower() or "a"
            remaining = entries
            if scope == "s":
                needle = prompt("Suchwort/URL-Teil: ").strip().lower()
                remaining = [e for e in entries if needle not in e["url"].lower()]
            elif scope == "z":
                try:
                    start_str = prompt("Ab Datum (YYYY-MM-DD), leer=egal: ").strip()
                    end_str = prompt("Bis Datum (YYYY-MM-DD), leer=egal: ").strip()
                    start_ts = datetime.fromisoformat(start_str).timestamp() if start_str else None
                    end_ts = datetime.fromisoformat(end_str).timestamp() if end_str else None
                    remaining = [
                        e for e in entries
                        if (start_ts is None or e["created_at"] >= start_ts)
                        and (end_ts is None or e["created_at"] <= end_ts)
                    ]
                except Exception:
                    print("Datum ungültig, abbreche.")
                    return
            state_store.save_twitter_history([e["url"] for e in remaining])
            print(f"{len(entries) - len(remaining)} gelöscht, {len(remaining)} übrig.")

        elif target == "n":
            username = prompt("Welcher Nutzer? (leer=alle): ").strip()
            entries = state_store.get_nitter_history_entries(username or None)
            print(f"{len(entries)} Einträge gefunden.")
            scope = prompt("Alle (a), nach Stichwort (s), Zeitraum (z)? [a/s/z]: ").strip().lower() or "a"
            remaining = entries
            if scope == "s":
                needle = prompt("Suchwort/URL-Teil: ").strip().lower()
                remaining = [e for e in entries if needle not in e["url"].lower()]
            elif scope == "z":
                try:
                    start_str = prompt("Ab Datum (YYYY-MM-DD), leer=egal: ").strip()
                    end_str = prompt("Bis Datum (YYYY-MM-DD), leer=egal: ").strip()
                    start_ts = datetime.fromisoformat(start_str).timestamp() if start_str else None
                    end_ts = datetime.fromisoformat(end_str).timestamp() if end_str else None
                    remaining = [
                        e for e in entries
                        if (start_ts is None or e["created_at"] >= start_ts)
                        and (end_ts is None or e["created_at"] <= end_ts)
                    ]
                except Exception:
                    print("Datum ungültig, abbreche.")
                    return
            new_map: dict[str, list[str]] = {}
            for entry in remaining:
                new_map.setdefault(entry["username"], []).append(entry["url"])
            state_store.save_nitter_history(new_map)
            print(f"{len(entries) - len(remaining)} gelöscht, {len(remaining)} übrig.")

        elif target == "b":
            feed_name = prompt("Feed-Name (z.B. viz_berlin): ").strip()
            if not feed_name:
                print("Kein Feed-Name angegeben.")
                return
            entries = state_store.load_bsky_entries(feed_name)
            print(f"{len(entries)} Einträge gefunden.")
            scope = prompt("Alle (a), nach Stichwort (s)? [a/s]: ").strip().lower() or "a"
            remaining = entries
            if scope == "s":
                needle = prompt("Suchwort/URL-Teil: ").strip().lower()
                remaining = [e for e in entries if needle not in e.lower()]
            state_store.save_bsky_entries(feed_name, remaining)
            print(f"{len(entries) - len(remaining)} gelöscht, {len(remaining)} übrig.")
        else:
            print("Unbekanntes Ziel.")


def main():
    parser = argparse.ArgumentParser(description="Einfache Verwaltung für nitter_bot.db")
    parser.add_argument("--db-path", help="Pfad zur Datenbank (setzt NITTER_DB_PATH)")
    args = parser.parse_args()

    resolved_db = args.db_path or os.environ.get("NITTER_DB_PATH") or storage.DB_PATH
    os.environ["NITTER_DB_PATH"] = resolved_db
    print(f"Nutze Datenbank: {resolved_db}")

    storage.init_db()
    state_store.prune_logs()

    while True:
        show_menu()
        choice = prompt("Auswahl: ").strip()

        if choice == "0":
            break

        elif choice == "1":
            print("\nTabellenübersicht:")
            for name, (bucket, default_key) in BUCKETS.items():
                key_info = f" (Standard-Schlüssel: {default_key})" if default_key else ""
                print(f"- {name}: {bucket}{key_info}")

        elif choice == "2":
            bucket, default_key = choose_bucket()
            if not bucket:
                continue
            if bucket == "twitter_history":
                entries = state_store.get_twitter_history_entries()
                print(f"{len(entries)} Einträge gefunden.")
                scope = prompt("Alle anzeigen (a), nach Stichwort (s), Zeitraum (z)? [a/s/z]: ").strip().lower() or "a"
                filtered = entries
                if scope == "s":
                    needle = prompt("Suche nach (Teil der URL): ").strip().lower()
                    filtered = [e for e in entries if needle in e["url"].lower()]
                elif scope == "z":
                    try:
                        start_str = prompt("Ab Datum (YYYY-MM-DD), leer=egal: ").strip()
                        end_str = prompt("Bis Datum (YYYY-MM-DD), leer=egal: ").strip()
                        start_ts = datetime.fromisoformat(start_str).timestamp() if start_str else None
                        end_ts = datetime.fromisoformat(end_str).timestamp() if end_str else None
                        filtered = [
                            e for e in entries
                            if (start_ts is None or e["created_at"] >= start_ts)
                            and (end_ts is None or e["created_at"] <= end_ts)
                        ]
                    except Exception:
                        print("Datum ungültig, zeige alle.")
                        filtered = entries
                print(pretty([e["url"] for e in filtered]))
            elif bucket == "nitter_history":
                username = prompt("Welcher Nutzer? (leer=alle): ").strip()
                entries = state_store.get_nitter_history_entries(username or None)
                print(f"{len(entries)} Einträge gefunden.")
                scope = prompt("Alle (a), nach Stichwort (s), Zeitraum (z)? [a/s/z]: ").strip().lower() or "a"
                filtered = entries
                if scope == "s":
                    needle = prompt("Suche nach (Teil der URL): ").strip().lower()
                    filtered = [e for e in entries if needle in e["url"].lower()]
                elif scope == "z":
                    try:
                        start_str = prompt("Ab Datum (YYYY-MM-DD), leer=egal: ").strip()
                        end_str = prompt("Bis Datum (YYYY-MM-DD), leer=egal: ").strip()
                        start_ts = datetime.fromisoformat(start_str).timestamp() if start_str else None
                        end_ts = datetime.fromisoformat(end_str).timestamp() if end_str else None
                        filtered = [
                            e for e in entries
                            if (start_ts is None or e["created_at"] >= start_ts)
                            and (end_ts is None or e["created_at"] <= end_ts)
                        ]
                    except Exception:
                        print("Datum ungültig, zeige alle.")
                        filtered = entries
                print(pretty([e["url"] for e in filtered]))
            else:
                data = load_bucket(bucket)
                print(pretty(data))

        elif choice == "3":
            bucket, default_key = choose_bucket()
            if not bucket:
                continue
            key = prompt(f"Key (default {default_key}): ").strip() or (default_key or "")
            if not key:
                print("Kein Key angegeben.")
                continue
            val = read_value(bucket, key)
            print(pretty(val))

        elif choice == "4":
            bucket, default_key = choose_bucket()
            if not bucket:
                continue
            key = prompt(f"Name des Eintrags (default {default_key}): ").strip() or (default_key or "")
            if not key:
                print("Kein Key angegeben.")
                continue
            raw = prompt("Neuer Inhalt (als Text oder JSON): ")
            set_value(bucket, key, raw)
            print("Gespeichert.")

        elif choice == "5":
            bucket, default_key = choose_bucket()
            if not bucket:
                continue
            if bucket == "twitter_history":
                entries = state_store.get_twitter_history_entries()
                mode = prompt("Löschen nach Zeit (t), nach Suchwort (s) oder alles (a)? [t/s/a]: ").strip().lower() or "a"
                remaining = entries
                if mode == "t":
                    start_str = prompt("Älter als Datum (YYYY-MM-DD) behalten ab? (leer=alles löschen): ").strip()
                    end_str = prompt("Bis Datum (YYYY-MM-DD) löschen? (leer=keine Obergrenze): ").strip()
                    try:
                        start_ts = datetime.fromisoformat(start_str).timestamp() if start_str else None
                        end_ts = datetime.fromisoformat(end_str).timestamp() if end_str else None
                        remaining = [
                            e for e in entries
                            if (start_ts is None or e["created_at"] >= start_ts)
                            and (end_ts is None or e["created_at"] <= end_ts)
                        ]
                    except Exception:
                        print("Datum ungültig, abbreche.")
                        continue
                elif mode == "s":
                    needle = prompt("Suchwort/URL-Teil zum Löschen: ").strip().lower()
                    remaining = [e for e in entries if needle not in e["url"].lower()]
                else:
                    remaining = []
                state_store.save_twitter_history([e["url"] for e in remaining])
                print(f"{len(entries) - len(remaining)} gelöscht, {len(remaining)} übrig.")
            elif bucket == "nitter_history":
                username = prompt("Welcher Nutzer? (leer=alle): ").strip()
                entries = state_store.get_nitter_history_entries(username or None)
                mode = prompt("Löschen nach Zeit (t), nach Suchwort (s) oder alles (a)? [t/s/a]: ").strip().lower() or "a"
                remaining = entries
                if mode == "t":
                    start_str = prompt("Älter als Datum (YYYY-MM-DD) behalten ab? (leer=alles löschen): ").strip()
                    end_str = prompt("Bis Datum (YYYY-MM-DD) löschen? (leer=keine Obergrenze): ").strip()
                    try:
                        start_ts = datetime.fromisoformat(start_str).timestamp() if start_str else None
                        end_ts = datetime.fromisoformat(end_str).timestamp() if end_str else None
                        remaining = [
                            e for e in entries
                            if (start_ts is None or e["created_at"] >= start_ts)
                            and (end_ts is None or e["created_at"] <= end_ts)
                        ]
                    except Exception:
                        print("Datum ungültig, abbreche.")
                        continue
                elif mode == "s":
                    needle = prompt("Suchwort/URL-Teil zum Löschen: ").strip().lower()
                    remaining = [e for e in entries if needle not in e["url"].lower()]
                else:
                    remaining = []

                # Map rekonstruieren
                new_map: dict[str, list[str]] = {}
                for entry in remaining:
                    new_map.setdefault(entry["username"], []).append(entry["url"])
                state_store.save_nitter_history(new_map)
                print(f"{len(entries) - len(remaining)} gelöscht, {len(remaining)} übrig.")
            else:
                key = prompt(f"Name des Eintrags (default {default_key}): ").strip() or (default_key or "")
                if not key:
                    print("Kein Key angegeben.")
                    continue
                confirm = prompt(f"Soll {bucket}/{key} gelöscht werden? (ja/nein): ").strip().lower()
                if confirm in {"y", "yes", "ja"}:
                    delete_key(bucket, key)
                    print("Gelöscht.")
                else:
                    print("Abgebrochen.")

        elif choice == "6":
            bucket, default_key = choose_bucket()
            if not bucket:
                continue
            key = prompt(f"Name des Eintrags (default {default_key}): ").strip() or (default_key or "")
            if not key:
                print("Kein Key angegeben.")
                continue
            raw = prompt("Liste anfügen (JSON-Array oder einzelner Wert): ")
            append_list(bucket, key, raw)
            print("Angehängt.")
        elif choice == "7":
            manage_nitter_users()
        elif choice == "8":
            quick_delete_urls()

        else:
            print("Unbekannte Auswahl.")


if __name__ == "__main__":
    main()
