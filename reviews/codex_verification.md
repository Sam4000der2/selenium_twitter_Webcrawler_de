# Codex Verification: Issues #58, #59, #60

Datum: 2026-03-10
Scope: Unabhängige Problem-/Issue-Verifikation gegen den aktuellen Stand im Repo (inkl. uncommitted Änderungen).

## Ergebnis
0 Findings

## #58 Verifikation
Akzeptanzkriterium: Bei `Forbidden: bot was blocked by user` werden alle Telegram-Nutzerdaten automatisch gelöscht.

Geprüfte Implementierung:
- `modules/telegram_bot_module.py:105` erkennt Block-Fehler über `_is_blocked_by_user_error(...)`.
- `modules/telegram_bot_module.py:114` führt Cleanup über `_cleanup_blocked_chat(...)` aus:
  - `state_store.remove_telegram_chat(chat_id)`
  - `state_store.remove_failed_deliveries_for_target("telegram", chat_id)`
- `modules/telegram_bot_module.py:257` triggert Cleanup direkt im Sendefehlerpfad.
- `modules/state_store_module.py:122` und `modules/state_store_module.py:433` implementieren die Löschoperationen für User-State und Retry-Jobs.

Evidenz:
- `./venv/bin/python -m pytest -q tests-unit/test_state_store_telegram_cleanup.py` -> pass
- Zusätzlicher unabhängiger Runtime-Check (Fake-Bot wirft `Forbidden: bot was blocked by user`):
  - Chat-State (`chat_ids`, `filter_rules`) für blockierten Chat wird entfernt.
  - Zugehörige `failed_deliveries` für Telegram-Target werden gelöscht.
  - Pending-Retry-Snapshot für denselben blockierten Chat wird im selben Lauf nicht erneut gesendet (nur 1 tatsächlicher Send-Versuch).

Bewertung: Erfüllt.

## #59 Verifikation
Akzeptanzkriterium: Telegram Control Bot findet/zeigt INFO-Meldungen korrekt.

Geprüfte Implementierung:
- `modules/control_bot_utils_module.py:53` enthält den robusten Level-Parser (`DEBUG|INFO|WARNING|ERROR|CRITICAL`, mit/ohne `:`).
- `modules/control_bot_utils_module.py:88` stellt `split_log_level_and_body(...)` bereit.
- `bots/telegram_control_bot.py:591` nutzt diesen Parser in `split_level_and_body(...)`.
- `bots/telegram_control_bot.py:914` (`admin_infos_command`) filtert explizit mit `levels=("INFO",)`.

Evidenz:
- `./venv/bin/python -m pytest -q tests-unit/test_control_bot_utils_log_parser.py` -> pass
- Zusätzlicher unabhängiger Runtime-Check mit synthetischer Logdatei:
  - INFO im Format `INFO:...` und `INFO ...` wird von `read_last_errors_grouped_multi(..., levels=("INFO",))` korrekt gefunden.
  - `admin_infos_command(...)` liefert beide INFO-Treffer in der Bot-Ausgabe.

Bewertung: Erfüllt.

## #60 Verifikation
Akzeptanzkriterium: INFO-Level ist reduziert; verbose Detail-Logs sind DEBUG.

Geprüfte Änderungen (Diff- und Codeprüfung):
- Detailreiche Logs wurden in den betroffenen Modulen von `INFO` auf `DEBUG` abgesenkt, u. a.:
  - `bots/bsky_bot.py` (Feed-/History-Detailmeldungen)
  - `bots/nitter_bot.py` (Feed-Check/alte Einträge/Debug-Mode-History)
  - `bots/twitter_bot.py` (WebDriver-/Navigation-Details)
  - `modules/mastodon_bot_module.py` (Setup-/Main-/Client-/Fallback-Details)
  - `modules/gemini_helper_module.py` (Modellauflistung)
  - `modules/telegram_bot_module.py` (Retry-Erfolg im Pending-Retry-Loop)
- Zusätzlich sind noisy Third-Party-Logger in den relevanten Bots/Modulen auf `WARNING` begrenzt:
  - `httpx`, `httpcore`, `urllib3`, `telegram`

Evidenz:
- `./venv/bin/python -m pytest -q tests-unit/test_paths_log_level.py` -> pass
- Unabhängige statische Verifikation per `rg`/Diff bestätigt die Umstellung der genannten Detail-Logs auf `DEBUG`.

Bewertung: Erfüllt.

## Schlussfazit
0 Findings
