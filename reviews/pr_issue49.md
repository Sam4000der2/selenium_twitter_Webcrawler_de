## Summary
Separate dev tooling dependencies from runtime dependencies, document QA workflow, and align local runbook hygiene with current repo state.

## Checks
- `./venv/bin/python -m compileall -q -x '(^|/)venv($|/)' .`
- `./venv/bin/pytest -q tests tests-unit`
- `./venv/bin/ruff check .`
- Codex reviews: `reviews/codex_code_review.md` = `0 Findings`, `reviews/codex_verification.md` = `0 Findings`

## Modules touched
- `requirements-dev.txt`
- `README.md`
- `.gitignore`
- `AGENTS.md`

Fixes #49
