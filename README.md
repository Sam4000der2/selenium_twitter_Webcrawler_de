**selenium_twitter_Webcrawler_de**

Dies ist ein Python-Bot, der ohne Twitter API arbeitet und über die Module Telegram_Bot und Mastodon_Bot alle Toots weiterleitet. Der Bot bietet die Option zur Filterung nach Stichworten wie spezifische Linien oder Orte in Telegram.

**Anleitung:**

**Schritt 1:** Installiere Python mit pip.

**Schritt 2:** Installiere die Module selenium, mastodon.py und python-telegram-bot mit pip.

**Schritt 3A:** Falls du Twitterdaten ohne Einloggen crawlen möchtest:
  - Kommentiere in der Datei `twitter_bot.py` folgende Änderungen aus:
    I) "firefox_profile = webdriver.FirefoxProfile(firefox_profile_path)"
    II) "firefox_options.profile = firefox_profile"
    III) Entferne den Kommentar für "driver = webdriver.Firefox(options=firefox_options)" in der `main()` Funktion.
    IV) Kommentiere "driver = webdriver.Firefox(options=firefox_options, firefox_profile=firefox_profile_path)" aus.
    V) Kommentiere auch `delete_temp_files()` aus.

**Schritt 3B:** Falls du auf nicht öffentlich sichtbare Twitterseiten zugreifen möchtest:
  - Passe den `firefox_profile_path` in der Datei `twitter_bot.py` an.

**Schritt 4:** Füge die gewünschte Twitterseite in `twitter_bot.py` hinzu und kommentiere nicht benötigte Module aus.

**Schritt 5:** Füge API-Keys für Telegram und Mastodon hinzu:
  I) API-Keys für Telegram bekommst du von https://t.me/BotFather.
  II) API-Key für Mastodon erhältst du unter deine_Instant.bspw_social/settings/applications.

**Schritt 6:** Führe den Bot testweise im Ordner des Bots aus: `python twitter_bot.py`.

**Schritt 7:** Füge im `telegram_controll_bot.py` den API-Key hinzu und ändere den Ordnerpfad.

**Schritt 8:** Richte die Bots als Service ein, um sie dauerhaft im Hintergrund laufen zu lassen.

**Schritt 9:** Herzlichen Glückwunsch, der Bot sollte nun laufen.

Vielen Dank an https://github.com/shaikhsajid1111/twitter-scraper-selenium/blob/main/twitter_scraper_selenium/element_finder.py für die Bereitstellung der CSS-Sektoren zur Tweet-Erfassung. Dieses Projekt eignet sich als Fertiglösung für Anfänger, die Profile crawlen möchten, jedoch sind diese oft nicht mehr chronologisch sortiert. Mein Ansatz bietet mehr Freiheit bei der Nutzung von Twitterlisten und Twitterseiten.
