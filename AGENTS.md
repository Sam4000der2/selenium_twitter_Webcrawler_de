# AGENTS.md

Diese Datei ist für **OpenAI Codex** gedacht (Codex CLI / Codex IDE). Codex liest `AGENTS.md` automatisch, bevor es im Repo arbeitet. citeturn0search0turn0search9

---

## 1) Berechtigungen & Grenzen

- **Arbeitsbereich:** Der Agent darf **alle Dateien innerhalb dieses Repositories** (aktueller geöffneter Ordner) lesen/erstellen/ändern/löschen.
- **Git:** Der Agent darf **`git commit`** ausführen, sobald die Definition of Done erfüllt ist.
  - `git push` nur, wenn ausdrücklich verlangt oder wenn der Projektprozess es eindeutig fordert.
- **Wichtige Grenzen:**
  - Nichts außerhalb des Repo-Ordners anfassen.
  - **Keine Secrets committen** (API-Keys, Tokens, Passwörter, private Zertifikate, `.env`-Inhalte). Nutze `*.example` + Umgebungsvariablen.
  - Keine destruktiven / History-rewrite Git-Aktionen ohne explizite Anweisung (`reset --hard`, force-push, riskante Rebase/History-Rewrites).
  - Keine repo-weiten “Mechanical” Änderungen (Mass-Formatierung), außer es ist Teil der Aufgabe.

---

## 2) Issue Handling (du nutzt keine Issues – Agent darf welche anlegen)

- Standardmäßig gibt es keine vorgegebenen GitHub Issues.
- Wenn es hilft (Akzeptanzkriterien, Repro-Schritte, Nachvollziehbarkeit), darf der Agent **selbst eine GitHub Issue anlegen** und anschließend lösen.
- Falls kein GitHub-Zugriff möglich ist: Lege stattdessen eine lokale Issue-Notiz an, z. B. `docs/issues/<slug>.md`, mit:
  - Problemstatement
  - Repro-Schritte
  - Expected Behavior
  - Akzeptanzkriterien (Definition of Done für das konkrete Problem)

---

## 3) Definition of Done (verbindlich)

Ein Task gilt erst als “Done”, wenn **alle** Punkte erfüllt sind:

1. **Implementierung 100% fertig**
   - Problem vollständig gelöst, keine Placeholder/TODOs als Ersatz.
   - Keine Debug-Prints, keine auskommentierten “temporären” Blöcke.

2. **Projekt-Checks grün**
   - Linting / Type-Checks / Tests laufen erfolgreich mit den Projekt-Tools (siehe Commands unten).

3. **Review-Prozess grün (Codex Sub-Agents)**
   - Zwei unabhängige Codex-Reviewläufe mit **0 Findings** (Details unten).

4. **Git Hygiene**
   - Saubere, verständliche Commits (klein genug zum Review, groß genug um sinnvoll zu sein).
   - Keine generierten Artefakte / riesige Binärdateien / Modelle einchecken, außer ausdrücklich gewollt.

---

## 4) Mandatory Review Process (Codex-spezifisch)

Codex unterstützt Multi-Agent-Orchestrierung (Sub-Agents). citeturn0search1

### Reviewer-Regeln (kritisch)

- **Kein Timeout** für Reviewer.
- Wenn ein Reviewer abbricht/fehlschlägt (z. B. “out of tokens”, killed, sonstiger Fehler): zählt **NICHT** → erneut ausführen.
- **CRITICAL:** Niemals Reviewer-stdout per Shell in eine Datei pipen (`>`, `| tee` etc.).
  - Stattdessen im Prompt anweisen: “Schreibe Findings in Datei X.”
  - Der Reviewer erstellt/überschreibt die Datei selbst.

### Die 2 verpflichtenden Codex-Reviews

1) **Codex Review – Code (uncommitted changes)**
- Sub-Agent soll die aktuellen uncommitted changes reviewen:
  - Korrektheit, Edge-Cases, Security, Wartbarkeit, Tests, Style, Breaking Changes
- Output in: `reviews/codex_code_review.md`
- Pass-Kriterium: Reviewer schreibt explizit `0 Findings`

2) **Codex Review – Problem/Issue Verifikation**
- Sub-Agent soll verifizieren, dass das Problem wirklich gelöst ist:
  - gegen Akzeptanzkriterien / Repro-Schritte
  - falls GitHub-Issue angelegt wurde: gegen diese Issue verifizieren
- Output in: `reviews/codex_verification.md`
- Pass-Kriterium: `0 Findings`

### Strikte Pass/Fail-Regel

- Wenn **irgendein** Reviewer Findings hat:
  1. **Alle** Findings fixen (auch scheinbar “unrelated”)
  2. Danach **beide Reviews erneut** laufen lassen
- “Done” erst, wenn **beide** Reports sauber sind.


## 5) Repository Guidelines

## Project Structure & Module Organization
- this Project folder `bots/`: Social bots and monitors (see `bots/AGENTS.md` for bot-specific runbooks). Kopie von /home/sascha/bots. nitter_bot.py und bsky_feed_monitor.py sind die aktiv laufende Bots. twitter_bot.py ist der legacy Vorgänger vom nitter_bot.py, alle anderen Python Scripte sind Module. Projekt per Git synchronisiert.

## Build, Test, and Development Commands  
  **Tests:** `./venv/bin/pytest tests tests-unit`.
- **Bots:**  
  `source ./venv/bin/activate && python3 -m pytest`  
  Run pipelines via `python3 nitter_bot.py` / `python3 mastodon_bot.py` after setting env tokens and Firefox profile.
## Coding Style & Naming Conventions
- **Python:** 4-space indent, type hints where stable; use `ruff` for linting (as configured in this repo). Names in `snake_case`, constants `UPPER_SNAKE`.
- **Nim:** run `nimpretty` on touched files; prefer `camelCase` procs and `TitleCase` types.
- Use ASCII unless a file already contains localized text (e.g., German docs).

## Testing Guidelines
- Add Python tests as `tests/test_*.py`; avoid live network/model calls—use dummy inputs or recorded fixtures.
- When touching multiple modules, note the exact commands executed (see above) in the PR/Notes.

## Commit Guidelines
- Commit messages: short, imperative (e.g., “Add Flux2 downloader retry”, “Fix Mastodon tag filter”).
- Do not commit generated models, checkpoints, `data.json`, or secrets.
- Keep config samples under version control (`*.example`); load secrets via env vars or local `.env`.

---

## 6) Output/Logs

- Review-Logs liegen unter `reviews/` (an Repo-Policy anpassen: ggf. `.gitignore`).
- In Commit-Body oder PR-Notes kurz notieren:
  - welche Checks gelaufen sind (Lint/Type/Tests)
  - welche Module betroffen waren
