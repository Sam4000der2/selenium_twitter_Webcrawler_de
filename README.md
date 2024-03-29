**Selenium Twitter Webcrawler - deutsche Version**

 ***[TO THE ENGLISH VERSION](https://github.com/Sam4000der2/selenium_twitter_Webcrawler_en)***
---

***Achtung:***

Das Modul Selenium unterstützt scheinbar keine Snap Pakete, bitte verzichtet darauf die Nutzung der Distribution Ubuntu in Kombination mit dem Projekt. Die Distributionen Mint und Debian sollten funktionieren. Die Nutzung von flatpak für Firefox habe ich in Kombination mit dem Projekt nicht getestet.

An sich funktioniert auch Chrome mit Selenium, die Verknüpfung scheint aber buggiger zu sein.

---

**Übersicht:**

Dieser Bot ermöglicht das Crawlen von Twitter-Daten ohne die Verwendung der Twitter-API. Er leitet alle Tweets über die Module Telegram_Bot und Mastodon_Bot weiter. Bei Telegram gibt es die Funktion der optionalen Filterung nach Stichworten wie spezifischen Linien oder Orten. Zur Steuerung des Telegram-Bots gibt es zusätzlich einen Control-Bot, der es ermöglicht, Chat-IDs und Filterbegriffe anzulegen und sich über den Benutzer bedienen zu lassen.

**Anleitung:**

**Schritt 1:** Installiere Python mit pip.

**Schritt 2:** Installiere mit pip die Module selenium, mastodon.py und python-telegram-bot.

**Schritt 3A:** Falls du Twitterdaten ohne Einloggen crawlen möchtest, nimm in der Datei `twitter_bot.py` folgende Änderungen vor:

- Kommentiere `firefox_profile = webdriver.FirefoxProfile(firefox_profile_path)` aus.
- Kommentiere `firefox_options.profile = firefox_profile` aus.
- In der `def main()` Funktion: Entferne den Kommentar für `driver = webdriver.Firefox(options=firefox_options)`.
- Kommentiere dafür `driver = webdriver.Firefox(options=firefox_options, firefox_profile=firefox_profile_path)` aus.
- Kommentiere auch `delete_temp_files()` aus, da dies voraussichtlich nicht mehr benötigt wird.

**Schritt 3B:** Falls du auf nicht öffentlich sichtbare Twitterseiten zugreifen möchtest, wie chronologisch sortierte Listen, passe in der Datei `twitter_bot.py` den `firefox_profile_path` an. Deinen Namen von deinem aktuellen Profil erfährst du unter `about:profiles`.

**Schritt 4:** Füge die gewünschte Twitterseite, deren Tweets du haben möchtest, in `twitter_bot.py` hinzu und kommentiere nicht benötigte Module aus:

- Falls du den Telegram Bot nicht benötigst, kommentiere in der `def main()` `await telegram_bot.main(new_tweets)` aus.
- Falls du den Telegram Bot nicht benötigst, kommentiere in der `def main()` `mastodon_bot.main(new_tweets)` aus.

**Schritt 5:** Füge in den Telegram-Bots und den Mastodon-Bot die API-Keys hinzu:

- Bei Telegram bekommst du die API-Keys vom Account https://t.me/BotFather, dort kannst du deine Bots auch einstellen.
- Für Mastodon bekommst du den API-Key unter `deine_Instant.bspw_social/settings/applications` (im Einstellungsmenü unter Entwicklung). Vergiss nicht, passende Rechte zu vergeben, sonst erhältst du schnell einen 403 Fehler. Für jede Änderung an den vergebenen Rechten musst du den API-Key neu generieren lassen. Vergiss auch nicht, deine Instanz ins Script einzutragen.

**Schritt 6:** Führe testweise im Ordner des Bots den Bot aus: `python twitter_bot.py`.

- Im besten Fall wird das Modul selenium den passenden Geckodriver für Firefox automatisch installieren.
- Falls nicht: Lade die passende Geckodriver-Version herunter (x64: [https://github.com/mozilla/geckodriver/releases](https://github.com/mozilla/geckodriver/releases) bzw. arm: [https://github.com/jamesmortensen/geckodriver-arm-binaries/releases](https://github.com/jamesmortensen/geckodriver-arm-binaries/releases)). Entpacke den Geckodriver und kopiere ihn aus den geöffneten Ordner mit dem Befehl `sudo cp geckodriver /usr/local/bin/geckodriver` in den passenden Ordner.

**Schritt 7:** Falls du den Telegram Bot nutzt, füge auch im `telegram_controll_bot.py` den API-Key hinzu. Am besten statt `DATA_FILE = 'data.json'` eine absolute Pfadangabe zu deinem Botordner verwenden, vergiss dann nicht, dies auch in `telegram_bot.py` zu ändern.

**Schritt 8:** Richte die Bots als Service ein, dann können sie dauerhaft im Hintergrund laufen.

- `sudo nano /etc/systemd/system/twitter_bot.service`

```plaintext
[Unit]
Description=twitter_bot
After=network.target

[Service]
WorkingDirectory=/[Ordnerpfad zu deinem Botordner]
ExecStart=python twitter_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

- `sudo systemctl daemon-reload`
- `sudo systemctl start twitter_bot.service`
- `sudo systemctl enable twitter_bot.service`
- Mache es mit dem `telegram_controll_bot` analog.

**Schritt 9:** Herzlichen Glückwunsch, der Bot sollte nun laufen.

**Schritt 10:** Falls gewünscht eine RAM Disc anlegen.

Eine RAM-Disk kann für temporäre Dateien in `/tmp` und `/var/tmp` verwendet werden, um die Geschwindigkeit zu erhöhen und die Festplatte/SSD/Speicherkarte zu schonen. Gerade da der Bug mit den andauernde Kopien anlegen des Firefox Profils noch nicht gelöst ist.

**Schritt 11:** RAM-Disk-Größe festlegen

Entscheide, wie viel Speicherplatz du der RAM-Disk zuweisen möchtest. In diesem Beispiel verwenden wir jeweils 1 1/2 GB für `/tmp` und `/var/tmp`.

**Schritt 12:** RAM-Disk einrichten

Öffne ein Terminal und führe die folgenden Befehle aus:

```bash
sudo mount -t tmpfs -o size=1536M tmpfs /tmp
sudo mount -t tmpfs -o size=1536M tmpfs /var/tmp
```

Dies erstellt separate RAM-Disks für `/tmp` und `/var/tmp` mit jeweils 1 1/2 GB Größe.

**Schritt 13:** Automatisches Einhängen der RAM-Disks

Um sicherzustellen, dass die RAM-Disks beim Start automatisch eingehängt werden, bearbeite die Datei `/etc/fstab`:

```bash
sudo nano /etc/fstab
```

Füge die folgenden Zeilen am Ende der Datei hinzu:

```
tmpfs   /tmp   tmpfs   size=1536M   0   0
tmpfs   /var/tmp   tmpfs   size=1536M   0   0
```

Speichere und schließe die Datei.

**Schritt 14:** Neustart des Systems

Starte dein System neu, um sicherzustellen, dass die Änderungen wirksam werden:

```bash
sudo reboot
```

Nach diesen Schritten sollten separate RAM-Disks für `/tmp` und `/var/tmp` mit jeweils 1 1/2 GB Größe eingerichtet sein und automatisch beim Start des Systems gemountet werden.

---

Vielen Dank an [https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py](https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py), dank diesem Projekt bin ich an die CSS-Sektoren gekommen, um die Tweets zu erhalten. Das Projekt eignet sich als Fertiglösung für Anfänger, die nur Profile crawlen wollen. Diese sind jedoch inzwischen oft nicht mehr chronologisch sortiert. Deshalb mein Ansatz mit den Twitterlisten und mehr Freiheit der Twitterseiten.
