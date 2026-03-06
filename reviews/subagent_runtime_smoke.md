# Sub-Agent B Runtime/Smoke-Check

Datum: 2026-03-06
Scope: Start-/Service-Skripte, Runtime-Defaults, lokale Smoke-Checks ohne Live-Tokens.

## Summary
- Ergebnis: **Findings vorhanden** (siehe unten).
- Hauptthemen: harte Pfade, Restart-Loop-Risiko bei `Restart=always`, fragile ENV-Parsing-Defaults, Root-Smoke-Command nicht robust.
- `systemd`-Syntax und Shell-Syntax sind formal ok, aber mehrere Runtime-Risiken bleiben.

## Checks ausgeführt (inkl. Output-Kernaussagen)
1. `python3 -m compileall .`
- Resultat: **Exit 1**.
- Kernaussage: schlägt in `venv` fehl (z. B. `venv/.../asyncio/base_events.py` und `venv/.../logging/__init__.py` mit Python-2/alt-Syntaxfehlern).

2. `python3 -m compileall $(rg --files -g '*.py' -g '!venv/**' -g '!__pycache__/**' -g '!.git/**')`
- Resultat: **OK** (`compileall(project .py files): OK`).
- Kernaussage: Projekt-Python-Dateien selbst kompilieren lokal.

3. `bash -n rotate_twitter_log.sh`
- Resultat: **OK**.

4. `systemd-analyze verify services/*.service`
- Resultat: **OK** (kein Verify-Output, Exit 0).

5. Startup-CLI-Smoke (venv)
- `./venv/bin/python nitter_bot.py --help` -> **OK** (Argparse-Help ausgegeben).
- `./venv/bin/python bsky_feed_monitor.py --help` -> **OK** (Argparse-Help ausgegeben).
- `./venv/bin/python telegram_control_bot.py --help`, `mastodon_control_bot.py --help`, `twitter_bot.py --help` starten echte Runtime-Loops (kein Argparse-Help); gestartete `--help`-Prozesse wurden gezielt wieder beendet.

6. Negativtests für Restart-/Crash-Risiken
- `env -u telegram_token -u telegram_admin timeout 8 ./venv/bin/python telegram_control_bot.py` -> **Exit 0** (sofortiger Programmexit).
- `env -u opnv_berlin -u opnv_toot -u opnv_mastodon timeout 8 ./venv/bin/python mastodon_control_bot.py` -> **Exit 0** (sofortiger Programmexit).
- `env NITTER_POLL_INTERVAL=abc ./venv/bin/python nitter_bot.py --help` -> **Exit 1**, `ValueError` bei `int(...)`.
- `env MASTODON_CONTROL_EVENT_PORT=abc ./venv/bin/python mastodon_control_bot.py` -> **Exit 1**, `ValueError` bei `int(...)`.
- `env BOTS_BASE_DIR=/tmp/definitely-missing-bots-path ./venv/bin/python nitter_bot.py --help` -> **Exit 1**, `FileNotFoundError` auf `.../twitter_bot.log`.

## Findings
1. **HIGH**: Harte absolute Runtime-Pfade in Service-Units (`/home/sascha/bots`) machen Deployments außerhalb dieses Pfads fragil/brechend.
- Dateien/Zeilen: `services/twitter_bot.service:7`, `services/twitter_bot.service:8`, `services/nitter_bot.service:7`, `services/nitter_bot.service:8`, `services/bsky_bot.service:7`, `services/bsky_bot.service:8`, `services/telegram_control_bot.service:7`, `services/telegram_control_bot.service:8`, `services/mastodon_control_bot.service:7`, `services/mastodon_control_bot.service:8`.
- Risiko: Service startet gar nicht oder sofortige Restart-Kaskade bei Pfadabweichung.

2. **HIGH**: `Restart=always` kombiniert mit "sauberem" Exit bei fehlender Konfiguration erzeugt Restart-Loop ohne echte Fehlereskalation.
- Dateien/Zeilen: `services/telegram_control_bot.service:9`, `services/mastodon_control_bot.service:9`, `telegram_control_bot.py:1347`, `telegram_control_bot.py:1349`, `mastodon_control_bot.py:2319`, `mastodon_control_bot.py:2321`, `mastodon_control_bot.py:2405`, `mastodon_control_bot.py:2407`.
- Evidenz: beide Bots ohne Tokens mit Exit-Code 0 beendet.

3. **MEDIUM**: Fragiles `int()`-ENV-Parsing ohne Guard kann Prozesse beim Start sofort crashen lassen.
- Dateien/Zeilen: `nitter_bot.py:26`, `nitter_bot.py:27`, `nitter_bot.py:29`, `mastodon_control_bot.py:21`, `mastodon_control_bot.py:66`.
- Evidenz: `NITTER_POLL_INTERVAL=abc` und `MASTODON_CONTROL_EVENT_PORT=abc` führen reproduzierbar zu `ValueError` + Exit 1.

4. **MEDIUM**: Log-Pfad-Initialisierung ist nicht robust gegen fehlende Basisverzeichnisse.
- Dateien/Zeilen: `nitter_bot.py:24`, `nitter_bot.py:81` (gleiches Muster auch in `bsky_feed_monitor.py:28`, `twitter_bot.py:43`, `telegram_bot.py:16`, `mastodon_bot.py:88`, `mastodon_control_bot.py:31`, `telegram_control_bot.py:33`).
- Evidenz: ungültiges `BOTS_BASE_DIR` führt direkt zu `FileNotFoundError` bei `WatchedFileHandler`.

5. **MEDIUM**: Runtime-Defaults und Doku sind inkonsistent (Nitter Base URL/Intervall).
- Dateien/Zeilen: `nitter_bot.py:26` (Default 60s), `nitter_bot.py:54` (Default `http://localhost:8081`) vs. `README.md:46`/`README.md:47` (Default `http://localhost:8080`, Poll 900s).
- Risiko: Fehlkonfiguration im Betrieb, unerwartete Poll-Frequenz/Target.

6. **LOW**: Root-Smoke-Befehl `python -m compileall .` ist im aktuellen Setup nicht robust, weil `venv` mit geprüft wird.
- Dateien/Zeilen: n/a (Tooling/Repo-Struktur).
- Evidenz: erster Lauf endet mit Syntaxfehlern in Drittanbieterpaketen unter `venv/`.

## Mapping zu Issues #1-#6
- **#1 Security: Block SSRF in URL expansion** -> Kein direkter neuer Befund aus diesem Runtime-Smoke-Check.
- **#2 Security: Prevent local-file read/internal SSRF media pipeline** -> Kein direkter neuer Befund aus diesem Runtime-Smoke-Check.
- **#3 Ops: Remove hardcoded `/home/sascha/bots` paths** -> **Direkt betroffen** durch Finding 1 und 4.
- **#4 Logging: Fix rotation / robust flow** -> **Teilweise betroffen** durch Finding 4 (unrobuste Logpfad-Initialisierung) und rotatorabhängige harte Pfade (`rotate_twitter_log.sh:4`).
- **#5 Ops/Docs: Align defaults, secure env-file handling, avoid restart loops** -> **Direkt betroffen** durch Findings 2, 3, 5.
- **#6 Quality: clean lint debt, remove broken legacy module, add root smoke test** -> **Direkt betroffen** durch Finding 6 (Root-Smoke-Command-Verhalten).

## 0 Findings Status
- **Nicht erreicht** (Findings vorhanden).
