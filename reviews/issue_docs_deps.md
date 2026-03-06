## Beschreibung
Runbook/Doku und Dependency-Setup sind inkonsistent: Tests werden als Standard-Check gefordert, aber `pytest` fehlt in `requirements.txt`; zusätzlich enthält AGENTS veraltete Pfadangaben.

## Repro-Schritte
1. Frische venv: `python3 -m venv .tmp-venv && source .tmp-venv/bin/activate`
2. `pip install -r requirements.txt`
3. `pytest --version` oder `python -m pytest`
4. Prüfe AGENTS-Hinweise auf `~/venv` / `bsky_monitor.py`

## Logs / Stacktrace
```text
/bin/bash: pytest: command not found
```

## Impact
- Onboarding/QA-Checks schlagen in frischer Umgebung fehl.
- Erhöht Reibung und Fehlersuche.

## Fix-Idee
- Dev-Dependencies klar trennen (`requirements-dev.txt`) und dokumentieren.
- AGENTS-Referenzen auf tatsächliche Dateinamen/Pfade aktualisieren.
