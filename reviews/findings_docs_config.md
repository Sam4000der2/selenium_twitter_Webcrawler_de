# Docs / Config Findings

## Summary
Es wurde ein dokumentations-/konfigurationsbezogenes Konsistenzproblem gefunden, das den Standard-Testablauf bricht.

## Checked files
- `AGENTS.md`
- `README.md`
- `requirements.txt`
- `services/*.service`

## Findings
1. **MEDIUM** - `AGENTS.md` (Build/Test commands)
- Beschreibung: Der dokumentierte Test-Command `pytest tests tests-unit` referenziert einen Pfad, der im Repo nicht vorhanden ist.
- Repro:
  1. `cd /home/sascha/Dokumente/bots`
  2. `./venv/bin/pytest -q tests tests-unit`
  3. Ergebnis: `ERROR: file or directory not found: tests-unit`
- Impact: Onboarding und DoD-Checks sind irreführend und nicht reproduzierbar.

## Suggested fix ideas
- `tests-unit/` anlegen und mit mindestens einem Unit-Test befüllen.
- Doku bei Bedarf präzisieren, welche Test-Sets verpflichtend sind.
