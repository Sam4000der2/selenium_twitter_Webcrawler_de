# Sub-Agent D Review (Docs/Config/Deps)

## Summary
- Scope geprüft: `README.md`, `requirements.txt`, `services/*.service`, relevante Runtime-Defaults in Python-Modulen.
- Ergebnis: **6 Findings** (2 hoch, 3 mittel, 1 niedrig).
- Bestehende Issues #3 und #5 sind weiterhin klar reproduzierbar; zusätzlich wurden neue Doku/Service-Risiken gefunden.
- `requirements.txt` deckt die im Code genutzten Kern-Runtime-Pakete grundsätzlich ab (kein offensichtlicher fehlender Pflicht-Import gefunden).

## Findings

### 1) Harte absolute Pfade auf `/home/sascha/bots` in Doku, Services und Code
- Severity: **High**
- Repro/Why:
  - README fordert/zeigt mehrfach feste Pfade auf `/home/sascha/bots` (z. B. `README.md:24`, `README.md:68`, `README.md:111`, `README.md:124`).
  - Alle systemd-Units nutzen harte `WorkingDirectory`/`ExecStart`-Pfade (`services/nitter_bot.service:7-8`, analog in allen anderen Units).
  - Laufzeit nutzt weiterhin harte Defaults, z. B. `storage.py:11`, `telegram_bot.py:12`, `mastodon_bot.py:88`, `store_twitter_logs.py:7`.
- Impact:
  - Deployments außerhalb genau dieses Pfads sind fragil oder schreiben in falsche Dateien/DBs.
  - Risiko der Vermischung von Test-/Produktivdaten.
- Fix idea:
  - Basisverzeichnis zentral ableiten (`BOTS_BASE_DIR` + `Path(__file__).resolve().parent` als Default).
  - Services auf parametrisierte Pfade umstellen (Environment + konsistente relative Starts).

### 2) Nitter-Default in README stimmt nicht mit Runtime überein
- Severity: **Medium**
- Repro/Why:
  - README nennt als Default `http://localhost:8080` (`README.md:13`, `README.md:46`).
  - Code nutzt `http://localhost:8081` (`nitter_bot.py:54`).
- Impact:
  - Frischer Betrieb nach README kann gegen falschen Port laufen und keine Feeds liefern.
- Fix idea:
  - README und Code auf denselben Default bringen.
  - Optional `NITTER_BASE_URL` explizit im Env-File-Beispiel dokumentieren.

### 3) Restart-Loop-Risiko bei fehlender Konfiguration (Exit 0 + `Restart=always`)
- Severity: **High**
- Repro/Why:
  - `telegram_control_bot` bricht bei fehlendem Token mit `return` ab (`telegram_control_bot.py:1347-1349`), ohne non-zero Exit.
  - `mastodon_control_bot` kann ohne sinnvolle Instanz-Tasks ebenfalls nur `return`en (`mastodon_control_bot.py:2407-2409`).
  - Service-Units setzen `Restart=always` (`services/telegram_control_bot.service:9`, `services/mastodon_control_bot.service:9`, analog weitere).
- Impact:
  - Endlose Restart-Zyklen, Log-Spam, erschwerte Fehlerdiagnose.
- Fix idea:
  - Bei Pflicht-ENV-Fehlern mit non-zero beenden oder `Restart=on-failure` + `StartLimit*` nutzen.

### 4) Secrets-Handling in README unsicher dokumentiert
- Severity: **Medium**
- Repro/Why:
  - README erstellt `/etc/twitter_bot.env` via `sudo tee ...` ohne Rechtehärtung (`README.md:95-109`).
- Impact:
  - Secrets können mit Standard-Umask zu weit lesbar sein.
- Fix idea:
  - Sichere Erstellung dokumentieren (z. B. `install -m 600 /dev/null /etc/twitter_bot.env` + anschließend befüllen).

### 5) README verweist auf nicht existente Config-Optionen/Legacy-Konstanten
- Severity: **Low**
- Repro/Why:
  - README fordert `delete_temp_files`-Deaktivierung (`README.md:7`, `README.md:43`), im Repo existiert diese Option nicht (`rg`-Treffer nur README).
  - README erwähnt `filename`/`RULES_FILE` als anzupassende Konstanten (`README.md:34`), diese spielen im aktuellen Codepfad keine Rolle.
- Impact:
  - Operatoren suchen/ändern falsche Stellen; unnötiger Aufwand und Fehlkonfiguration.
- Fix idea:
  - README auf aktuelle, tatsächlich verwendete Konfig-Keys reduzieren.

### 6) Service-Startreihenfolge nur `network.target` statt `network-online.target`
- Severity: **Medium**
- Repro/Why:
  - Alle Units setzen nur `After=network.target` (z. B. `services/nitter_bot.service:3`).
  - Bots sind netzabhängig (Telegram/Mastodon/Nitter/Bluesky APIs).
- Impact:
  - Frühstarts beim Boot vor stabiler Netzverfügbarkeit führen zu vermeidbaren Fehlern/Restarts.
- Fix idea:
  - Units auf `Wants=network-online.target` + `After=network-online.target` umstellen.

## Mapping zu Issues #1-#6
- #1 (SSRF URL expansion): **Kein direkter Docs/Config-Finding in diesem Review**.
- #2 (Media pipeline SSRF/local file): **Kein direkter Docs/Config-Finding in diesem Review**.
- #3 (hardcoded base dir paths): **Abgedeckt durch Finding 1**.
- #4 (log rotation flow): **Kein neuer Docs/Config-Befund in Scope dieser Runde**.
- #5 (runtime defaults/env perms/restart loops): **Abgedeckt durch Findings 2, 3, 4**.
- #6 (quality/lint/legacy/tests): **Kein direkter Docs/Config-Finding; nur indirekte Doku-Drift in Finding 5**.

## New-Issue-Kandidaten (nicht #1-#6)
- Finding 5: README enthält veraltete/irrelevante Konfig-Hinweise.
- Finding 6: systemd-Netz-Ordering nicht boot-robust.
