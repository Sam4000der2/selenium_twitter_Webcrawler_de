# Sub-Agent C Security/Secrets Review

Stand: 2026-03-06  
Repo: `/home/sascha/Dokumente/bots`  
Scope: SSRF/LFI/Path Traversal, unsafe URL-Handling, Secret-Leaks, sensibles Logging, `.gitignore`, Env-Handling

## Summary
- Ergebnis: **3 Findings** (2x High, 1x Medium).
- Die offenen Security-Issues **#1** und **#2** sind im aktuellen Stand weiterhin reproduzierbar (ungefixt).
- Kein harter Secret-Commit (Token/API-Key/Private Key) in getrackten Dateien gefunden.
- `.gitignore` deckt `.env` und `*.log` ab, aber Env-Operational-Härtung in der Doku ist weiterhin unzureichend.

## Findings

### F1: SSRF über untrusted URL-Expansion (weiterhin offen)
- Severity: **High**
- Kategorie: SSRF / unsafe URL handling
- Betrifft: `twitter_bot.py`, `nitter_bot.py`
- Repro:
  1. Einen externen Post/Feed mit Link wie `http://127.0.0.1:8123/health` oder `http://169.254.169.254/latest/meta-data/` einspeisen.
  2. Bot-Pipeline ruft `expand_short_urls()` auf.
  3. Bot macht aktive Requests inkl. Redirect-Following ohne Zielvalidierung.
- Impact:
  - Interne Services/Metadaten-Endpunkte vom Bot-Host aus erreichbar (SSRF).
  - Potenzieller Informationsabfluss und interne Netzwerkerkundung.
- Fix-Idee:
  - Zentrale URL-Validierung vor jedem Request und nach Redirects.
  - Private/loopback/link-local/reserved/multicast Ziele blockieren.
  - Optional nur `https` + explizite Allowlist für interne Sonderfälle.
- Evidenz (file/line):
  - `twitter_bot.py:82` (`requests.head(... allow_redirects=True ...)`)
  - `twitter_bot.py:94` (`requests.get(... allow_redirects=True ...)`)
  - `nitter_bot.py:373` (`requests.head(... allow_redirects=True ...)`)
  - `nitter_bot.py:385` (`requests.get(... allow_redirects=True ...)`)

### F2: LFI + SSRF in Media-Pipeline (weiterhin offen)
- Severity: **High**
- Kategorie: Local File Read + SSRF + unsafe URL handling
- Betrifft: `nitter_bot.py`, `mastodon_bot.py`
- Repro:
  1. Manipulierten Feed-Eintrag mit z. B. `<img src="/etc/passwd">` oder `<img src="http://127.0.0.1:8123/secret.jpg">` bereitstellen.
  2. `nitter_bot.parse_summary()` übernimmt `src` in `images` ohne interne Zielprüfung.
  3. `mastodon_bot.prepare_media_payloads()` liest lokale Dateien via `os.path.isfile(...)`/`aiofiles.open(...)` oder lädt URLs via `session.get(...)`.
- Impact:
  - Lokale Dateien können gelesen und weiterverarbeitet/gepostet werden (LFI).
  - SSRF gegen interne HTTP-Endpunkte aus der Media-Pipeline.
- Fix-Idee:
  - Lokale Dateipfade in dieser Pipeline vollständig verbieten.
  - Nur strikt validierte öffentliche Media-URLs zulassen (oder enge Allowlist + Pfad-Whitelist).
  - Einheitliche URL-Härtung für Bild- und Video-Download.
- Evidenz (file/line):
  - `nitter_bot.py:627` bis `nitter_bot.py:633` (`src`-Übernahme ohne `is_internal_url`)
  - `mastodon_bot.py:1325` (`os.path.isfile(image_link)`)
  - `mastodon_bot.py:1327` (lokales File-Read via `aiofiles.open`)
  - `mastodon_bot.py:1057` (`session.get(url)` in `download_image`)
  - `mastodon_bot.py:1090` (`session.get(url)` in `download_binary`)

### F3: Env-Handling in Doku weiterhin unsicher (Dateirechte für Secrets)
- Severity: **Medium**
- Kategorie: Secret exposure / env handling
- Betrifft: `README.md`
- Repro:
  1. Env-Datei gemäß README via `sudo tee /etc/twitter_bot.env` erstellen.
  2. Ohne explizites `chmod 600` hängen die Rechte von `umask` ab und können zu breit sein.
- Impact:
  - Lokale Nutzer auf dem Host könnten Secret-Datei mitlesen (abhängig von effektiven Dateirechten).
- Fix-Idee:
  - Erstellung mit sicheren Rechten dokumentieren, z. B. `install -m 600 /dev/null /etc/twitter_bot.env` + anschließend befüllen.
  - Alternativ explizit `chmod 600 /etc/twitter_bot.env` als Pflichtschritt.
- Evidenz (file/line):
  - `README.md:95` bis `README.md:109` (Env-Datei-Anlage ohne Rechtehärtung)
  - Service-Nutzung dieser Datei: `services/twitter_bot.service:6` (analog in weiteren Services)

## Mapping zu bestehenden Issues #1-#6
- **#1 Security: Block SSRF in URL expansion for twitter_bot and nitter_bot**  
  Status im Code: **Offen / ungefixt**. Abgedeckt durch **F1**.
- **#2 Security: Prevent local-file read and internal SSRF in media pipeline**  
  Status im Code: **Offen / ungefixt**. Abgedeckt durch **F2**.
- **#3 Ops: Remove hardcoded /home/sascha/bots paths...**  
  Security-Mapping: Kein direkter SSRF/LFI/Secret-Finding in diesem Review.
- **#4 Logging: Fix rotation with WatchedFileHandler...**  
  Security-Mapping: Kein direkter SSRF/LFI/Secret-Finding in diesem Review (Issue-Fokus ist Rotation/FD-Verhalten).
- **#5 Ops/Docs: secure env-file handling...**  
  Status im Code/Doku: **Offen / ungefixt**. Abgedeckt durch **F3**.
- **#6 Quality: lint/legacy/tests**  
  Security-Mapping: Kein direkter SSRF/LFI/Secret-Finding in diesem Review.

## Zusatzcheck: Secrets/.gitignore
- `.gitignore` enthält `.env` und `*.log` (`.gitignore:3`, `.gitignore:8`).
- Bei Stichproben/Regex-Scan keine harten API-Keys/Tokens/Private-Keys in getrackten Dateien gefunden.

