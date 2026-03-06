## Beschreibung
Telegram-Konfiguration verwendet nur lower-case ENV-Namen (`telegram_token`, `telegram_admin`), während der Rest des Projekts primär `UPPER_SNAKE_CASE` nutzt.

## Repro-Schritte
1. `cd /home/sascha/Dokumente/bots`
2. `env -u telegram_token -u telegram_admin TELEGRAM_TOKEN=bar TELEGRAM_ADMIN=2 ./venv/bin/python - <<'PY'\nimport telegram_bot\nprint(repr(telegram_bot.BOT_TOKEN), repr(telegram_bot.admin))\nPY`

## Logs / Stacktrace
```text
None None
```

## Impact
- Fehlkonfiguration bei Standard-Deployment-Konventionen.
- Inkonsistentes Namensschema über Module hinweg.

## Fix-Idee
- UPPERCASE-Varianten zusätzlich unterstützen (mit Backward-Compatibility).
- README/Service-Beispiele entsprechend ergänzen.
