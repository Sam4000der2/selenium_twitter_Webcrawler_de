## Problem
Die projektdokumentierte Testausführung `pytest tests tests-unit` ist aktuell nicht lauffähig, weil das Verzeichnis `tests-unit/` im Repo fehlt.

## Repro Steps
1. `cd /home/sascha/Dokumente/bots`
2. `./venv/bin/pytest -q tests tests-unit`

## Logs / Stacktrace
```text
ERROR: file or directory not found: tests-unit

no tests ran in 0.00s
```

## Impact
- Definierte DoD-/Review-Commands sind nicht reproduzierbar.
- Automatisierung/CI scheitert auf documented command path statt auf echtem Test-Fehler.

## Fix Idea
- `tests-unit/` anlegen und mit mindestens einem stabilen Unit-Test ergänzen.
- Optional Doku konsolidieren, damit nur vorhandene Test-Sets referenziert werden.

## Acceptance Criteria
- `./venv/bin/pytest -q tests tests-unit` läuft ohne "file not found".
- Die neuen Unit-Tests sind deterministisch und ohne externe Netzabhängigkeit.
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .` ist erfolgreich.
