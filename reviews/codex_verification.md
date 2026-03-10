## Acceptance Criteria
1. Nitter DB files are ignored.
2. SQLite sidecar files (`wal`/`shm`) are ignored.
3. No Nitter DB file is currently tracked.

## Evidence
- Ignore rules are present in [.gitignore:15](/home/sascha/Dokumente/bots/.gitignore:15) and [.gitignore:16](/home/sascha/Dokumente/bots/.gitignore:16):
  - `*.db`
  - `*.db-*`
- `git check-ignore -v` confirms ignore matches for Nitter DB and sidecars:
  - `config/nitter_bot.db` -> `.gitignore:15:*.db`
  - `config/nitter_bot.db-wal` / `config/nitter_bot.db-shm` -> `.gitignore:16:*.db-*`
  - `modules/nitter_bot.db` -> `.gitignore:15:*.db`
  - `modules/nitter_bot.db-wal` / `modules/nitter_bot.db-shm` -> `.gitignore:16:*.db-*`
- Repo-wide tracked-file checks returned no matches:
  - `git ls-files ':(glob)**/*nitter*.db' ':(glob)**/*nitter*.db-*' ':(glob)**/*nitter*.sqlite*'` -> no output
  - `git ls-files config/nitter_bot.db ... modules/nitter_bot.db-shm` -> no output

## Findings
0 Findings
