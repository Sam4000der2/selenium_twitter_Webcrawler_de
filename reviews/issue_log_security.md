## Beschreibung
Log-Rotation erzeugt zu offene Dateien und Archive können versehentlich committed werden.

## Repro-Schritte
1. `cd /home/sascha/Dokumente/bots`
2. `./rotate_twitter_log.sh`
3. `stat -c '%a %n' twitter_bot.log logs/twitter_bot.log.*`
4. Prüfe `.gitignore` auf Muster für `*.log.*`

## Logs / Stacktrace
- Kein Stacktrace erforderlich.
- Aktuell wird neues Log via `install -m 644 /dev/null "$LOGFILE"` erzeugt.
- `.gitignore` enthält `*.log`, aber nicht `*.log.*`.

## Impact
- Historische Logs können sensible Betriebsdaten enthalten und zu weit lesbar sein.
- Risiko von versehentlichen Log-Commits.

## Fix-Idee
- Rechte auf `600` setzen und umask härten.
- `.gitignore` um `*.log.*` und `logs/` ergänzen.
