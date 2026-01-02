# ÖPNV Social Bots (Twitter/X & Bluesky → Telegram/Mastodon)

Dieses Verzeichnis enthält die Bots, die ÖPNV-Meldungen von Twitter/X (per Selenium-Webdriver) und Bluesky-RSS-Feeds abholen und an Telegram sowie mehrere Mastodon-Instanzen ausliefern. Alt-Texte werden automatisch über Google Gemini generiert; Filter/Tagging lassen sich per Control-Bots steuern.

## Wichtige Hinweise
- Keine Snap-Pakete nutzen (Selenium + Snap machen Probleme); Firefox via Flatpak ist nicht getestet, Chrome ist möglich aber instabiler.
- Twitter/X-Listen brauchen in der Regel einen eingeloggten Account. Nutze ein Firefox-Profil (`about:profiles`) und passe `firefox_profile_path` an. Falls die Zielseite öffentlich ist, kannst du ohne Login arbeiten, indem du das Profil im Code entfernst (Profile-Zeilen auskommentieren) und optional `delete_temp_files` deaktivierst.
- Geckodriver muss verfügbar sein (`/usr/local/bin/geckodriver` voreingestellt). Falls Selenium ihn nicht automatisch findet, manuell installieren/kopieren.

## Komponenten
- `twitter_bot.py`: Selenium-Scraper für eine X-Liste. Nutzt ein lokales Firefox-Profil, dedupliziert über `existing_tweets.txt` und sendet neue Tweets an Telegram und Mastodon.
- `bsky_feed_monitor.py`: Pollt konfigurierte Bluesky-RSS-Feeds (z. B. VIZ Berlin) und leitet neue Einträge an Telegram/Mastodon weiter.
- `telegram_bot.py`: Versendet Tweets/Feeds an alle in `data.json` hinterlegten Chats. Filterwörter pro Chat bestimmen, was zugestellt wird.
- `telegram_control_bot.py`: Telegram-Bot zur Verwaltung von Chat-IDs und Filtern (`/start`, `/status`, `/addfilterrules`, `/deletefilterrules`, `/deleteallrules`, `/list`, `/about`, `/datenschutz`). Admin-Kommandos erlauben Service-Meldungen an alle Kanäle und Log-Auszüge.
- `mastodon_bot.py`: Postet Tweets/Feeds auf `berlin.social`, `toot.berlin` und `mastodon.berlin` (Tokens aus ENV). Unterstützt Bilder/Videos, generiert Alt-Texte via Gemini und taggt Nutzer basierend auf `mastodon_rules.json`.
- `mastodon_control_bot.py`: Mastodon-DM-Bot zum Verwalten der Tagging-Regeln (`/start`, `/add`, `/list`, `/overview`, `/delete`, `/pause`, `/resume`, `/schedule`, `/stop`). Lauscht optional auf Events vom Posting-Bot.
- `gemini_helper.py` + `test_alt_text.py`: Modellverwaltung für Gemini (Cache in `gemini_models.csv`) und Offline/Online-Test der Alt-Text-Generierung (`python test_alt_text.py --image <pfad> [--dummy]`).
- Daten/Logs: `data.json` (Telegram-Chat-IDs & Filter), `mastodon_rules.json` (Tagging-Regeln), `viz_berlin_entries.txt` und `existing_tweets.txt` (History), Log unter `/home/sascha/bots/twitter_bot.log`.

## Voraussetzungen & Installation
- Python 3 + virtuelles Environment (empfohlen):  
  `python3 -m venv /home/sascha/bots/venv && source /home/sascha/bots/venv/bin/activate`
- Abhängigkeiten installieren (im Ordner `bots/`):  
  `pip install -r requirements.txt`
- Firefox + Geckodriver (in `twitter_bot.py` aktuell `/usr/local/bin/geckodriver`). Nutze ein eingeloggtes Firefox-Profil für X (`firefox_profile_path`).
- Netz- und API-Zugänge per Environment:
  - Telegram: `telegram_token`, `telegram_admin`
  - Mastodon: `opnv_berlin`, `opnv_toot`, `opnv_mastodon`
  - Gemini: `GEMINI_API_KEY`
  - Optional: `MASTODON_CONTROL_EVENT_ENABLED|HOST|PORT`, `MASTODON_CONTROL_POLL_INTERVAL`

> Hinweis: Alle Skripte verwenden absolute Pfade auf `/home/sascha/bots/…`. Wenn das Repo anders liegt, passe die Konstanten (`firefox_profile_path`, `geckodriver_path`, `filename`, `DATA_FILE`, `RULES_FILE`, Log-Pfade) in den Skripten an.

## Konfiguration
- **Twitter/X-Scraper (`twitter_bot.py`)**
  - `twitter_link` auf die gewünschte Liste/Account setzen.
  - Firefox-Profil (`firefox_profile_path`) und Geckodriver-Pfad konfigurieren. Läuft headless und pollt alle 60 s.
  - Neue Links landen in `existing_tweets.txt`; `var_href` wird beim Telegram-Versand auf `nitter.net` umgeschrieben.
  - Kurz-URLs werden erweitert; Bilder/Videos und externe Links gehen an Mastodon weiter.
  - Optional ohne Login: Profil-Zuweisung in `twitter_bot.py` auskommentieren und `delete_temp_files` deaktivieren (siehe Hinweise oben).

- **Bluesky-Feeds (`bsky_feed_monitor.py`)**
  - FEEDS-Liste anpassen (`name`, `url`, `file`, optional `max_entries`). History pro Feed wird in der jeweiligen Datei gehalten.
  - Pollt alle 60 s und nutzt dieselben Bot-Module für die Auslieferung.

- **Telegram**
  - `data.json` legt `chat_ids` und `filter_rules` ab; wird vom Control-Bot gepflegt.
  - `telegram_bot.py` verschickt Nachrichten an alle Chat-IDs; Filterwörter pro Chat entscheiden über Zustellung.
  - `telegram_control_bot.py` bietet Nutzer-Kommandos (Start/Stop/Status/Filter) und Admin-Kommandos (Service-Meldungen, Log-Fehler/Warnungen).

- **Mastodon**
  - Tokens aus ENV; je Instanz wird Sichtbarkeit anhand bekannter Accounts gewählt.
  - Alt-Texte pro Bild/Video via Gemini (Fallback-Text bei Fehlern); Modelle werden gecached und Quoten respektiert.
  - Tagging-Regeln aus `mastodon_rules.json` (DM/Tag, Zeitfenster, Block-/Allow-Keywords). Verwaltung erfolgt über den Mastodon-Control-Bot.
  - Event-Brücke: `mastodon_bot` kann nach erfolgreichem Post per TCP an `MASTODON_CONTROL_EVENT_HOST:PORT` senden, damit `mastodon_control_bot` Status-Updates sieht.

## Starten
1) Virtuelle Umgebung aktivieren:  
   `source /home/sascha/bots/venv/bin/activate`

2) Bots starten (je nach Bedarf separate Prozesse/Services):
   - X-Scraper: `python twitter_bot.py`
   - Bluesky-Feeds: `python bsky_feed_monitor.py`
   - Telegram-Control: `python telegram_control_bot.py`
   - Mastodon-Control: `python mastodon_control_bot.py`

3) Alt-Texte testen (ohne Posten):  
   `python test_alt_text.py --dummy` (offline) oder `python test_alt_text.py --image <pfad>` mit gesetztem `GEMINI_API_KEY`.

## Services (systemd)
- Vorlagen liegen unter `services/`: `twitter_bot.service.txt`, `bsyk_bot.service.txt`, `telegram_control_bot.service.txt`, `mastodon_control_bot.service.txt`.
- Alle Einheiten referenzieren ein gemeinsames Env-File (`/etc/twitter_bot.env`) mit den nötigen Secrets, z. B.:
  ```bash
  GEMINI_API_KEY=...
  telegram_token=...
  telegram_admin=...
  opnv_berlin=...
  opnv_toot=...
  opnv_mastodon=...
  ```
- Env-File anlegen (für Service und lokale Tests):  
  ```bash
  sudo tee /etc/twitter_bot.env >/dev/null <<'EOF'
  GEMINI_API_KEY=DEIN_KEY
  telegram_token=DEIN_TELEGRAM_TOKEN
  telegram_admin=123456789
  opnv_berlin=TOKEN
  opnv_toot=TOKEN
  opnv_mastodon=TOKEN
  EOF
  ```
- Manuell testen ohne Service:  
  `set -a; source /etc/twitter_bot.env; set +a; cd /home/sascha/bots; python twitter_bot.py`  
  (analog für `bsky_feed_monitor.py`, `telegram_control_bot.py`, `mastodon_control_bot.py`).
- Installation (Beispiel `twitter_bot`):
  ```bash
  sudo cp services/twitter_bot.service.txt /etc/systemd/system/twitter_bot.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now twitter_bot.service
  ```
  Weitere Bots analog mit den jeweiligen Dateien aus `services/`.

## Logging, Daten & Betrieb
- Zentrales Log: `/home/sascha/bots/twitter_bot.log` (alle Module). Admin-Befehle in Telegram zeigen Auszüge.
- Historien/Caches werden automatisch geschrieben: `existing_tweets.txt`, `viz_berlin_entries.txt`, `gemini_models.csv`, `mastodon_rules.json`, `data.json`.
- Für Dauerbetrieb können systemd-Services genutzt werden (ExecStart z. B. `/home/sascha/bots/venv/bin/python /home/sascha/bots/twitter_bot.py`; ENV-Variablen im Service setzen).

## Danksagung
Dank an [shaikhsajid1111](https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py) für die CSS-Selector-Basis zum Extrahieren von Tweets.

Viel Erfolg mit den ÖPNV-Bots!
