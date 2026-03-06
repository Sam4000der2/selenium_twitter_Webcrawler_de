# Static Analysis Findings

## Summary
Es wurden reproduzierbare Findings gefunden. Hauptproblem aus dem statischen Check ist ein inkonsistenter Test-Command in den Repo-Richtlinien.

## Executed commands + key output
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .` -> erfolgreich
- `./venv/bin/ruff check . --exclude venv --output-format concise` -> `All checks passed!`
- `./venv/bin/pytest -q` -> `2 passed`
- `./venv/bin/pytest -q tests tests-unit` -> `ERROR: file or directory not found: tests-unit`

## Findings
1. **MEDIUM** - `AGENTS.md` (Testing Guidelines)
- Beschreibung: Der dokumentierte Pflicht-Command `pytest tests tests-unit` schlägt im aktuellen Repo fehl, weil `tests-unit/` nicht existiert.
- Repro:
  1. `cd /home/sascha/Dokumente/bots`
  2. `./venv/bin/pytest -q tests tests-unit`
  3. Beobachtung: `ERROR: file or directory not found: tests-unit`
- Impact: CI/Review- und DoD-Checks sind inkonsistent; reproduzierbare Quality-Gates brechen trotz funktionierender Tests.

## Suggested fix ideas
- Entweder `tests-unit/` inkl. mindestens einem Unit-Test anlegen.
- Oder die projektdokumentierten Test-Commands konsolidieren (nur vorhandene Pfade).
