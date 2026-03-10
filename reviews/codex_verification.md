# Codex Verification: Varianten-Lock Twitter/Nitter

Datum: 2026-03-10
Scope: Unabhängige Verifikation der aktuellen uncommitted Änderungen in `/home/sascha/Dokumente/bots` gegen die Anforderung:
- Nur eine aktive Sender-Instanz gleichzeitig
- Zweite Instanz nur Testmodus ohne Senden
- `twitter_bot` und `nitter_bot` als Varianten derselben Bot-Gruppe

## Ergebnis
**0 Findings**

## Geprüfte Änderungen
- `bots/twitter_bot.py`
- `bots/nitter_bot.py`
- `modules/bot_variant_guard_module.py` (neu)

## Verifikation je Anforderung

1. Nur eine aktive Sender-Instanz gleichzeitig
- Beide Bots rufen `variant_guard.try_acquire_sender_lock(...)` auf.
- Das Guard-Modul nutzt einen gemeinsamen Datei-Lock via `fcntl.flock(... LOCK_EX | LOCK_NB)` pro Gruppenname.
- Zwei-Prozess-Laufzeittest bestätigt: erste Instanz `can_send=true`, parallele zweite Instanz `can_send=false`.

2. Zweite Instanz nur Testmodus ohne Senden
- Bei Lock-Konflikt erzwingen beide Bots in `_enforce_variant_sender_lock(...)`:
  - `args.debug = True`
  - `args.no_send = False`
- Versandpfade sind in beiden Bots durch `if not args.debug and not args.no_send:` geschützt.
- Debug-Modus setzt `persist_history = False`; damit keine DB/History-Updates.

3. `twitter_bot` und `nitter_bot` als Varianten derselben Bot-Gruppe
- Beide Bots verwenden denselben Gruppennamen:
  - `_VARIANT_GROUP_NAME = "twitter_nitter_variant"`

## Ausgeführte Checks
- `./venv/bin/python -m ruff check bots/nitter_bot.py bots/twitter_bot.py modules/bot_variant_guard_module.py` -> pass
- `./venv/bin/python -m py_compile bots/nitter_bot.py bots/twitter_bot.py modules/bot_variant_guard_module.py` -> pass
- `./venv/bin/python -m pytest tests tests-unit` -> pass (`33 passed`)
- Isolierter Zwei-Prozess-Locktest -> pass (erste Instanz sendeberechtigt, zweite Instanz blockiert)
- Sanity-Check erzwungener Konfliktmodus (`_enforce_variant_sender_lock`) in beiden Bots -> `debug=True`, `no_send=False`

## Schlussfazit
**0 Findings**
