Hier findest du eine verbesserte und übersichtlichere Version deiner README-Datei:

---

# Selenium Twitter Webcrawler – Deutsche Version

[**Zur englischen Version**](https://github.com/Sam4000der2/selenium_twitter_Webcrawler_en)

---

## Wichtige Hinweise

- **Snap-Pakete:**  
  Selenium unterstützt offenbar keine Snap-Pakete. Verwende daher **nicht** die Ubuntu-Distribution in Kombination mit diesem Projekt – Mint und Debian haben sich bewährt.

- **Firefox via Flatpak:**  
  Die Nutzung von Firefox über Flatpak wurde in Verbindung mit diesem Projekt nicht getestet.

- **Google Chrome:**  
  Chrome funktioniert grundsätzlich mit Selenium, jedoch kann die Integration instabiler sein.

---

## Übersicht

Dieses Projekt ermöglicht das Crawlen von Twitter-Daten **ohne** die offizielle Twitter-API zu nutzen. Alle gefundenen Tweets werden automatisch über zwei Module weitergeleitet:

- **Telegram Bot:**  
  Zusätzlich mit optionaler Filterung (z. B. nach bestimmten Stichwörtern, Linien oder Orten).

- **Mastodon Bot:**  
  Einfache Weiterleitung der Tweets an Mastodon.

Zudem gibt es einen **Control-Bot** für Telegram, mit dem du Chat-IDs und Filterbegriffe verwalten und den Bot bedienen kannst.

---

## Installation & Konfiguration

### Voraussetzungen

- **Python** (inklusive pip)
- Folgende Python-Module (über pip installierbar):
  - siehe requirements.txt

### Schritt-für-Schritt-Anleitung

#### 1. Python und benötigte Module installieren

Stelle sicher, dass Python samt pip installiert ist. Installiere dann die erforderlichen Module:
```bash
pip install -r requirements.txt
```

#### 2. Anpassung für öffentlich zugängliche Twitter-Daten (ohne Login)

Falls du Twitter-Daten ohne Login crawlen möchtest, nimm in der Datei `twitter_bot.py` folgende Änderungen vor:

- **Auskommentieren:**
  ```python
  # firefox_profile = webdriver.FirefoxProfile(firefox_profile_path)
  # firefox_options.profile = firefox_profile
  ```
- **In der `def main()`-Funktion:**
  - Entferne den Kommentar von:
    ```python
    driver = webdriver.Firefox(options=firefox_options)
    ```
  - Kommentiere stattdessen:
    ```python
    # driver = webdriver.Firefox(options=firefox_options, firefox_profile=firefox_profile_path)
    ```
- **Zusätzlich:**  
  Kommentiere auch die Funktion `delete_temp_files()` aus, da sie in diesem Modus vermutlich nicht benötigt wird.

#### 3. Zugriff auf nicht öffentliche Twitter-Seiten (z. B. chronologisch sortierte Listen)

- Passe in der Datei `twitter_bot.py` den Wert von `firefox_profile_path` an, um auf geschützte oder personalisierte Seiten zugreifen zu können.
- Deinen Profilnamen findest du unter `about:profiles` in Firefox.

#### 4. Zielseiten und Modul-Auswahl

- **Twitter-Seiten hinzufügen:**  
  Trage in `twitter_bot.py` die Twitter-Seite ein, deren Tweets du erfassen möchtest.
- **Unnötige Module deaktivieren:**  
  Kommentiere in der `def main()` die Aufrufe des Telegram- bzw. Mastodon-Bots aus, wenn du einen der Dienste nicht benötigst:
  ```python
  # await telegram_bot.main(new_tweets)
  # mastodon_bot.main(new_tweets)
  ```

#### 5. API-Schlüssel einrichten

- **Telegram:**  
  Hole deine API-Schlüssel über [BotFather](https://t.me/BotFather) und füge diese in den entsprechenden Dateien ein.

- **Mastodon:**  
  Den API-Schlüssel findest du unter den Einstellungen deiner Instanz (im Bereich **Entwicklung**). Achte darauf, dass die erforderlichen Rechte vergeben sind – bei Änderungen musst du den API-Key neu generieren. Vergiss nicht, deine Instanz auch im Script anzugeben. Außerdem wird über die Gemini-API kostenlos Alt-Texte für die Bilder geniert. 

- **Gemini API (Testzwecke):**  
  Füge deinen Gemini API-Key in deine `~/.bashrc` ein. Öffne die Datei mit:
  ```bash
  nano ~/.bashrc
  ```
  und füge die Zeile hinzu:
  ```bash
  export GOOGLE_API_KEY="YOURAPIKEY"
  ```
  Deinen kostenlosen Gemini API-Key erhältst du hier: [Gemini API Key](https://aistudio.google.com/apikey).

#### 6. Testausführung des Bots

Führe den Bot im entsprechenden Verzeichnis testweise aus:
```bash
python twitter_bot.py
```
- **Hinweis:**  
  Selenium versucht in der Regel, den passenden Geckodriver für Firefox automatisch zu installieren. Sollte dies nicht funktionieren, lade den Geckodriver manuell herunter:
  - **x64 & ARM:** [Geckodriver Releases](https://github.com/mozilla/geckodriver/releases)
  
  Entpacke den Geckodriver und kopiere ihn in das Systemverzeichnis:
  ```bash
  sudo cp geckodriver /usr/local/bin/geckodriver
  ```

#### 7. Konfiguration des Telegram Control Bots

Falls du den Telegram Bot nutzt, füge in `telegram_controll_bot.py` deinen API-Schlüssel hinzu.  
Es wird empfohlen, statt `DATA_FILE = 'data.json'` einen absoluten Pfad zu verwenden – vergiss nicht, diese Änderung auch in `telegram_bot.py` zu übernehmen.

#### 8. Bots als Service einrichten

Um den Bot dauerhaft im Hintergrund laufen zu lassen, richte ihn als Systemdienst ein:

1. Erstelle eine Service-Datei:
   ```bash
   sudo nano /etc/systemd/system/twitter_bot.service
   ```
2. Füge folgenden Inhalt ein und passe `YOURUSER` sowie `YOURAPIKEY` an:
   ```ini
   [Unit]
   Description=twitter_bot
   After=network.target

   [Service]
   Environment="GEMINI_API_KEY=YOURAPIKEY"
   WorkingDirectory=/home/YOURUSER/bots
   ExecStart=/home/YOURUSER/bots/venv/bin/python3 /home/YOURUSER/bots/twitter_bot.py
   Restart=always
   RestartSec=10
   User=YOURUSER
   Group=YOURUSER

   [Install]
   WantedBy=multi-user.target
   ```
3. Lade die Systemdienste neu:
   ```bash
   sudo systemctl daemon-reload
   ```
4. Starte und aktiviere den Service:
   ```bash
   sudo systemctl start twitter_bot.service
   sudo systemctl enable twitter_bot.service
   ```
5. Richte den `telegram_controll_bot` analog ein.

#### 9. Abschluss

Herzlichen Glückwunsch – der Bot sollte nun erfolgreich laufen!

---

## Danksagung

Ein besonderer Dank geht an [shaikhsajid1111](https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py). Dank dieses Projekts konnte ich an die Verwendung von CSS-Selektoren herankommen, um Tweets zu extrahieren. Dieses Projekt eignet sich vor allem für Anfänger, die Profile crawlen möchten – auch wenn die chronologische Sortierung oft nicht mehr gegeben ist. Mit meinem Ansatz über Twitter-Listen biete ich mehr Flexibilität.

---

Viel Erfolg beim Einsatz des Selenium Twitter Webcrawlers!
