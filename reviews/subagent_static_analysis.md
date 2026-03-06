# Sub-Agent A Static Analysis / Lint Report

Datum: 2026-03-06
Scope: `*.py` im Repo-Root (ohne `venv/`), statische CLI-Analyse + gezielte Dateiinspektion

## Summary
Es wurden **4 relevante Findings** mit realem Impact gefunden (1x High, 2x Medium, 1x Low).

Ausgeführte Checks (repräsentativ):
- `python3 -m py_compile $(rg --files -g '*.py' -g '!venv/**')`
- `python3 -m compileall -q $(rg --files -g '*.py' -g '!venv/**')`
- `source venv/bin/activate && ruff check . --exclude venv --output-format concise`
- `venv/bin/python -c "import element_finder"`
- `rg -n "/home/sascha" --glob '*.py'`
- `venv/bin/python -m pytest -q`

## Findings

### 1) High: Harte absolute Pfade im Runtime-Code (`/home/sascha/bots`) machen Deployment fragil
- Severity: **High**
- Files/Lines (Auszug):
  - `bsky_feed_monitor.py:28`
  - `mastodon_bot.py:88`
  - `telegram_bot.py:12`, `telegram_bot.py:16`
  - `telegram_control_bot.py:20`, `telegram_control_bot.py:33`, `telegram_control_bot.py:39`, `telegram_control_bot.py:42`
  - `mastodon_control_bot.py:31`
  - `store_twitter_logs.py:7`
  - `storage.py:11`
  - `twitter_bot.py:43`
  - weitere Treffer via Fallback-Pattern in Control-Bots
- Repro:
  - `rg -n "/home/sascha" --glob '*.py'`
- Begründung/Impact:
  - Laufzeitpfade sind host-spezifisch verdrahtet; bei anderem Checkout-Pfad greifen Logs/DB/Config potenziell auf falsche Orte zu oder brechen.

### 2) Medium: `element_finder.py` ist import-broken und faktisch Legacy/Dead Module
- Severity: **Medium**
- Files/Lines:
  - `element_finder.py:5` (`from .scraping_utilities import Scraping_utilities`)
  - `element_finder.py:9` (`from .driver_utils import Utilities`)
- Repro:
  - `venv/bin/python -c "import element_finder"`
  - Ergebnis: `ImportError: attempted relative import with no known parent package`
  - Zusätzlich: `rg -n "element_finder|scraping_utilities|driver_utils" --glob '*.py'` zeigt nur Self-References.
- Begründung/Impact:
  - Modul ist im aktuellen Repo-Layout nicht importierbar und wird nicht genutzt; erzeugt technische Schuld und irreführenden Legacy-Code.

### 3) Medium: Keine root-Testabdeckung sammelbar (`pytest` findet 0 Tests)
- Severity: **Medium**
- File/Line: N/A (Projektzustand)
- Repro:
  - `venv/bin/python -m pytest -q`
  - Ergebnis: `no tests ran`
  - `venv/bin/python -m pytest tests tests-unit -q` -> `ERROR: file or directory not found: tests`
- Begründung/Impact:
  - Es fehlt ein minimaler automatisierter Smoke-Guard; statische/kleine Runtime-Regressions werden nicht automatisiert abgefangen.

### 4) Low: Ruff-Lint-Defizite (11 Befunde) bleiben offen
- Severity: **Low**
- Files/Lines (Auszug):
  - `bsky_feed_monitor.py:288` (`F841` ungenutzte Variable)
  - `telegram_bot.py:263` (`F841`)
  - `telegram_control_bot.py:794` (`F841`), `telegram_control_bot.py:1222` (`F541`)
  - `twitter_bot.py:4`, `:5`, `:7` (`F401`), `twitter_bot.py:376` (`F841`)
  - `mastodon_bot.py:580`, `mastodon_control_bot.py:535` (`E741`)
  - `storage.py:2` (`F401`)
- Repro:
  - `source venv/bin/activate && ruff check . --exclude venv --output-format concise`
- Begründung/Impact:
  - Kein sofortiger Crash, aber unnötiges Rauschen, potenziell versteckte Logikreste und schlechtere Wartbarkeit.

## Mapping zu bestehenden Issues #1-#6
- **#1 Security: Block SSRF in URL expansion**
  - Kein neues statisches Finding in diesem Lauf.
- **#2 Security: Prevent local-file read/internal SSRF in media pipeline**
  - Kein neues statisches Finding in diesem Lauf.
- **#3 Ops: Remove hardcoded `/home/sascha/bots` paths**
  - Voll getroffen durch Finding 1.
- **#4 Logging: Rotation/WatchedFileHandler**
  - Kein neues statisches Finding in diesem Lauf.
- **#5 Ops/Docs defaults/env-file/restart loops**
  - Kein neues eindeutiges statisches Finding in diesem Lauf.
- **#6 Quality: lint debt, broken legacy module, smoke test**
  - Direkt getroffen durch Findings 2, 3 und 4.
