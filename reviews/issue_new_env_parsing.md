## Problem
Mehrere ENV-Werte werden beim Start direkt mit `int(...)` geparst. Ungültige Werte (z. B. `abc`) führen sofort zu `ValueError` und Prozessabbruch. Das ist betrieblich fragil und erzeugt unnötige Crash-Loops bei kleinen Konfigurationsfehlern.

## Repro-Schritte
1. Starte den Bot mit fehlerhafter ENV-Konfiguration, z. B.:
   - `env NITTER_POLL_INTERVAL=abc ./venv/bin/python nitter_bot.py --help`
   - `env MASTODON_CONTROL_EVENT_PORT=abc ./venv/bin/python mastodon_control_bot.py`
2. Prozess bricht beim Parsing ab.

## Logs/Stacktrace
Typischer Fehler:
- `ValueError: invalid literal for int() with base 10: 'abc'`

## Impact
- Sofortiger Startabbruch bei ungültiger ENV-Eingabe.
- Höheres Risiko für Restart-Loops und instabile Deployments.

## Fix-Idee
- Zentrale robuste ENV-Parser einführen (`parse_int_env` o.ä.) mit Fallback-Default und Warn-Logging.
- Für Pflichtwerte klare Fehlermeldung + kontrollierter Exit-Code.

## Acceptance Criteria
- Ungültige numerische ENV-Werte crashen den Prozess nicht unkontrolliert.
- Default/Fallback-Verhalten ist dokumentiert und geloggt.
- `python -m compileall .` (bzw. projektweit ohne `venv`) erfolgreich.
