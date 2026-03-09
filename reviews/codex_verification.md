Issue #56 Verifikation (unabhaengig)

Akzeptanzkriterien:
1. Non-command mentions werden ignoriert (keine Bot-Antwort)
2. Explizite Slash-Befehle funktionieren
3. Offene Dialogzustaende (ja/nein) funktionieren weiterhin ohne Slash

Durchgefuehrte Verifikation:
- Codepfad-Pruefung in `mastodon_control_bot.py`:
  - Offene Dialoge werden zuerst verarbeitet (`handle_pending_state` vor Slash-Gate in `handle_command`).
  - Ausserhalb offener Dialoge werden nur explizite Slash-Befehle verarbeitet (`if not lower.startswith("/") : return` Logik vorhanden).
- Testlauf:
  - `BOTS_BASE_DIR=/tmp/bots-issue56-verification PYTHONDONTWRITEBYTECODE=1 ./venv/bin/python -m pytest -q -p no:cacheprovider tests-unit/test_mastodon_control_bot_commands.py`
  - Ergebnis: `5 passed`
- Isolierte Laufzeitverifikation (direkter Aufruf von `handle_command` mit Stubs):
  - `@controlbot Danke ...` -> keine Bot-Antwort
  - `@controlbot /status` -> Bot-Antwort mit Status
  - `@controlbot /help)` -> Bot-Antwort mit Help (Slash-Command mit nachgestellter Klammer funktioniert)
  - Offener `confirm_start`-Dialog + `ja` (ohne Slash) -> verarbeitet, Zustand entfernt
  - Offener `confirm_start`-Dialog + `nein` (ohne Slash) -> verarbeitet, Zustand entfernt

Ergebnis:
0 Findings
