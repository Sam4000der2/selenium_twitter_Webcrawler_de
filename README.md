# ÖPNV Social Bots (Twitter/X/Nitter & Bluesky → Telegram/Mastodon)

Dieses Verzeichnis enthält die Bots, die ÖPNV-Meldungen von Twitter/X (per Selenium-Webdriver) und Bluesky-RSS-Feeds abholen und an Telegram sowie mehrere Mastodon-Instanzen ausliefern. Alt-Texte werden automatisch über Google Gemini generiert; Filter/Tagging lassen sich per Control-Bots steuern.

## Wichtige Hinweise
- Keine Snap-Pakete nutzen (Selenium + Snap machen Probleme); Firefox via Flatpak ist nicht getestet, Chrome ist möglich aber instabiler.
- Twitter/X-Listen brauchen in der Regel einen eingeloggten Account. Nutze ein Firefox-Profil (`about:profiles`) und setze optional `TWITTER_FIREFOX_PROFILE_PATH`. Falls die Zielseite öffentlich ist, kannst du ohne Login arbeiten.
- Geckodriver muss verfügbar sein (Default `/usr/local/bin/geckodriver`). Bei abweichendem Pfad setze `TWITTER_GECKODRIVER_PATH`.
- Bestehende JSON-State-Dateien können mit `python -m tools.migrate_telegram_data_json_tool` nach `nitter_bot.db` migriert werden.

## Projektstruktur
- `bots/`: ausführbare Bot-Implementierungen (einheitlich mit `*_bot.py` bzw. `*_control_bot.py`)
- `modules/`: wiederverwendbare Bot-Module (einheitlich mit `_module.py`)
- `tools/`: CLI-/Wartungs-Skripte (einheitlich mit `_tool.py`)
- `scripts/`: Shell-Hilfsskripte für Betrieb/Automation
- `config/`: statische Vorlagen (z. B. `config/data.json.example`)

## Komponenten
- `bots/twitter_bot.py`: Selenium-Scraper für eine X-Liste. Nutzt ein lokales Firefox-Profil, dedupliziert über die gemeinsame SQLite-DB `nitter_bot.db` und sendet neue Tweets an Telegram und Mastodon.
- `bots/nitter_bot.py`: Pollt die lokale Nitter-Instanz (`http://localhost:8081/<user>/rss`) statt Selenium/X. History und Nutzer-Intervalle liegen in `nitter_bot.db` und sollen den Twitter-Bot langfristig ersetzen.
- `bots/bsky_bot.py`: Pollt konfigurierte Bluesky-RSS-Feeds (z. B. VIZ Berlin) und leitet neue Einträge an Telegram/Mastodon weiter.
- `modules/telegram_bot_module.py`: Versendet Tweets/Feeds an alle in der DB hinterlegten Chats. Filterwörter pro Chat bestimmen, was zugestellt wird.
- `bots/telegram_control_bot.py`: Telegram-Bot zur Verwaltung von Chat-IDs und Filtern (`/start`, `/status`, `/addfilterrules`, `/deletefilterrules`, `/deleteallrules`, `/list`, `/about`, `/datenschutz`). Admin-Kommandos erlauben Service-Meldungen an alle Kanäle und Log-Auszüge.
- `modules/mastodon_bot_module.py`: Postet Tweets/Feeds auf `berlin.social`, `toot.berlin` und `mastodon.berlin` (Tokens aus ENV). Unterstützt Bilder/Videos, generiert Alt-Texte via Gemini und taggt Nutzer basierend auf DB-Regeln.
- `bots/mastodon_control_bot.py`: Mastodon-DM-Bot zum Verwalten der Tagging-Regeln (`/start`, `/add`, `/list`, `/overview`, `/delete`, `/pause`, `/resume`, `/schedule`, `/stop`). Lauscht optional auf Events vom Posting-Bot.
- `modules/gemini_helper_module.py` + `tools/test_alt_text_tool.py`: Modellverwaltung für Gemini (Cache in der DB) und Offline/Online-Test der Alt-Text-Generierung (`python -m tools.test_alt_text_tool --image <pfad> [--dummy]`).
- Daten/Logs: zentrale SQLite-DB `nitter_bot.db` (Chat-Filter, Mastodon-Regeln, Gemini-Cache, Histories inkl. Mastodon-Posts) und Log unter `$BOTS_BASE_DIR/twitter_bot.log` (Default: aktueller Repo-Ordner).
- Legacy-Telegram-State: `data.json` ist eine lokale Laufzeitdatei (nicht versioniert). Im Repo liegt nur `config/data.json.example` als Vorlage.

## Voraussetzungen & Installation
- Python 3 + virtuelles Environment (empfohlen):  
  `export BOTS_BASE_DIR="$(pwd)" && python3 -m venv "$BOTS_BASE_DIR/venv" && source "$BOTS_BASE_DIR/venv/bin/activate"`
- Abhängigkeiten installieren (im Ordner `bots/`):  
  `pip install -r requirements.txt`
- Für lokale Checks/Testentwicklung zusätzlich Dev-Tools installieren:  
  `pip install -r requirements-dev.txt`
- Firefox + Geckodriver (Default-Pfad `/usr/local/bin/geckodriver`; optional ENV `TWITTER_GECKODRIVER_PATH`). Für eingeloggte X-Sessions optional `TWITTER_FIREFOX_PROFILE_PATH` setzen.
- Netz- und API-Zugänge per Environment:
  - Telegram: `TELEGRAM_TOKEN`, `TELEGRAM_ADMIN` (Legacy-Fallback: `telegram_token`, `telegram_admin`)
  - Mastodon: `opnv_berlin`, `opnv_toot`, `opnv_mastodon`
  - Gemini: `GEMINI_API_KEY` (optional zusätzlich: `GEMINI_API_KEY1` bis `GEMINI_API_KEY4` für Round-Robin)
  - Twitter/Selenium: `TWITTER_LIST_URL`, `TWITTER_GECKODRIVER_PATH`, optional `TWITTER_FIREFOX_PROFILE_PATH`
  - Optional: `MASTODON_CONTROL_EVENT_ENABLED|HOST|PORT`, `MASTODON_CONTROL_POLL_INTERVAL`
  - Logging zentral: `BOTS_LOG_LEVEL` (Fallback `LOG_LEVEL`, z. B. `DEBUG`, `INFO`, `WARNING`, `ERROR`)

> Hinweis: Laufzeitpfade werden zentral über `BOTS_BASE_DIR` gesteuert (Default: Repo-Ordner).  
  Die SQLite-DB kann über `NITTER_DB_PATH` umgezogen werden (Standard `$BOTS_BASE_DIR/nitter_bot.db`).

## Konfiguration
- **Twitter/X-Scraper (`bots/twitter_bot.py`)**
  - Quell-Liste per `TWITTER_LIST_URL` konfigurieren (Fallback: interner Default).
  - Firefox-Profil optional über `TWITTER_FIREFOX_PROFILE_PATH`, Geckodriver über `TWITTER_GECKODRIVER_PATH` (Default `/usr/local/bin/geckodriver`).
  - Neue Links werden in `nitter_bot.db` dedupliziert; `var_href` wird beim Telegram-Versand auf `nitter.net` umgeschrieben.
  - Kurz-URLs werden erweitert; Bilder/Videos und externe Links gehen an Mastodon weiter.
  - Optional ohne Login: `TWITTER_FIREFOX_PROFILE_PATH` nicht setzen.

- **Nitter-RSS (`bots/nitter_bot.py`)**
  - Arbeitet gegen `NITTER_BASE_URL` (Standard `http://localhost:8081`) und liest Accounts samt Intervallen/Zeitfenstern aus der DB (Default-Seed wie bisher).
  - Loop-Fallback-Sleep: 60 s (`NITTER_POLL_INTERVAL`), pro Account gelten die in der DB gespeicherten Intervalle (z. B. `SBahnBerlin` mit 120 s von 05:55–22:05).
  - Dedupliziert per DB-History und baut die Status-Links zu `x.com/<user>/status/<id>` für Telegram/Mastodon.
  - History-Limit per `NITTER_HISTORY_LIMIT` einstellbar; `NITTER_POLL_INTERVAL` steuert das Loop-Fallback-Sleep (min. 15 s).

- **Bluesky-Feeds (`bots/bsky_bot.py`)**
  - FEEDS-Liste anpassen (`name`, `url`, optional `max_entries`). History pro Feed wird in der DB gehalten.
  - Pollt alle 60 s und nutzt dieselben Bot-Module für die Auslieferung.

- **Telegram**
  - Chat-IDs und `filter_rules` werden in der DB gehalten; Verwaltung erfolgt über den Control-Bot.
  - `modules/telegram_bot_module.py` verschickt Nachrichten an alle Chat-IDs; Filterwörter pro Chat entscheiden über Zustellung.
  - `bots/telegram_control_bot.py` bietet Nutzer-Kommandos (Start/Stop/Status/Filter) und Admin-Kommandos (Service-Meldungen, Log-Fehler/Warnungen).
  - Einmalige Migration einer bestehenden `data.json`:  
    `python -m tools.migrate_telegram_data_json_tool --data-file ./data.json`  
    Optional: `--dry-run` (nur prüfen) oder `--force` (bestehende Telegram-Daten in DB überschreiben).

- **Mastodon**
  - Tokens aus ENV; je Instanz wird Sichtbarkeit anhand bekannter Accounts gewählt.
  - Alt-Texte pro Bild/Video via Gemini (Fallback-Text bei Fehlern); Modelle werden gecached und Quoten respektiert.
  - Tagging-Regeln liegen in der DB (DM/Tag, Zeitfenster, Block-/Allow-Keywords). Verwaltung erfolgt über den Mastodon-Control-Bot.
  - Event-Brücke: `modules/mastodon_bot_module.py` kann nach erfolgreichem Post per TCP an `MASTODON_CONTROL_EVENT_HOST:PORT` senden, damit `bots/mastodon_control_bot.py` Status-Updates sieht.

## Starten
1) Virtuelle Umgebung aktivieren:  
   `export BOTS_BASE_DIR="$(pwd)" && source "$BOTS_BASE_DIR/venv/bin/activate"`

2) Bots starten (je nach Bedarf separate Prozesse/Services):
   - X-Scraper: `python bots/twitter_bot.py`
   - Nitter-RSS (ohne Selenium): `python bots/nitter_bot.py`
   - Bluesky-Feeds: `python bots/bsky_bot.py`
   - Telegram-Control: `python bots/telegram_control_bot.py`
   - Mastodon-Control: `python bots/mastodon_control_bot.py`

3) Alt-Texte testen (ohne Posten):  
   `python -m tools.test_alt_text_tool --dummy` (offline) oder `python -m tools.test_alt_text_tool --image <pfad>` mit gesetztem `GEMINI_API_KEY` (optional plus `GEMINI_API_KEY1..4`).

4) Projekt-Checks lokal ausführen:
   - `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
   - `./venv/bin/pytest tests tests-unit`
   - `./venv/bin/ruff check .`

## Services (systemd)
- Vorlagen liegen unter `services/`: `twitter_bot.service`, `bsky_bot.service`, `telegram_control_bot.service`, `mastodon_control_bot.service`, `nitter_bot.service`.
- Alle Einheiten referenzieren ein gemeinsames Env-File (`/etc/twitter_bot.env`) mit den nötigen Secrets, z. B.:
  ```bash
  GEMINI_API_KEY=...
  GEMINI_API_KEY1=...
  GEMINI_API_KEY2=...
  GEMINI_API_KEY3=...
  GEMINI_API_KEY4=...
  TELEGRAM_TOKEN=...
  TELEGRAM_ADMIN=...
  opnv_berlin=...
  opnv_toot=...
  opnv_mastodon=...
  TWITTER_LIST_URL=https://x.com/i/lists/...
  TWITTER_GECKODRIVER_PATH=/usr/local/bin/geckodriver
  TWITTER_FIREFOX_PROFILE_PATH=/home/<user>/.mozilla/firefox/<profile>.Twitter
  ```
- Env-File anlegen (für Service und lokale Tests) mit restriktiven Rechten:  
  ```bash
  sudo install -m 600 /dev/null /etc/twitter_bot.env
  sudo tee /etc/twitter_bot.env >/dev/null <<'EOF'
  BOTS_BASE_DIR=/home/<user>/Dokumente/bots
  GEMINI_API_KEY=DEIN_KEY
  GEMINI_API_KEY1=OPTIONAL_KEY_1
  GEMINI_API_KEY2=OPTIONAL_KEY_2
  GEMINI_API_KEY3=OPTIONAL_KEY_3
  GEMINI_API_KEY4=OPTIONAL_KEY_4
  TELEGRAM_TOKEN=DEIN_TELEGRAM_TOKEN
  TELEGRAM_ADMIN=123456789
  opnv_berlin=TOKEN
  opnv_toot=TOKEN
  opnv_mastodon=TOKEN
  TWITTER_LIST_URL=https://x.com/i/lists/1901917316708778158
  TWITTER_GECKODRIVER_PATH=/usr/local/bin/geckodriver
  TWITTER_FIREFOX_PROFILE_PATH=/home/<user>/.mozilla/firefox/<profile>.Twitter
  EOF
  sudo chmod 600 /etc/twitter_bot.env
  ```
- Manuell testen ohne Service:  
  `set -a; source /etc/twitter_bot.env; set +a; export BOTS_BASE_DIR="$(pwd)"; python bots/twitter_bot.py`  
  (analog für `bots/bsky_bot.py`, `bots/telegram_control_bot.py`, `bots/mastodon_control_bot.py`).
- Installation (Beispiel `twitter_bot`):
  ```bash
  sudo cp services/twitter_bot.service /etc/systemd/system/twitter_bot.service
  sudo systemctl daemon-reload
  sudo systemctl enable --now twitter_bot.service
  ```
  Weitere Bots analog mit den jeweiligen Dateien aus `services/`.

## Logging, Daten & Betrieb
- Zentrales Log: `$BOTS_BASE_DIR/twitter_bot.log` (alle Module). Admin-Befehle in Telegram zeigen Auszüge.
- Zentrales Logging-Level für alle Bots: `BOTS_LOG_LEVEL` (oder `LOG_LEVEL`).
- Historien/Caches landen gesammelt in `nitter_bot.db` (Buckets u. a. für Twitter/Nitter-History, Bluesky-Feeds, Telegram-Filter, Mastodon-Regeln/-Posts, Gemini-Status).
- Für Dauerbetrieb können systemd-Services genutzt werden (ExecStart läuft über `$BOTS_BASE_DIR`; `BOTS_BASE_DIR` wird im `EnvironmentFile` gesetzt).

## Danksagung
Dank an [shaikhsajid1111](https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py) für die CSS-Selector-Basis zum Extrahieren von Tweets.

Viel Erfolg mit den ÖPNV-Bots!
