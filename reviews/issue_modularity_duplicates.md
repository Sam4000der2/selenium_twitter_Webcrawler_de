## Beschreibung
Mehrere Netzwerk-/Logging-Helfer sind zwischen `telegram_control_bot.py` und `mastodon_control_bot.py` nahezu identisch dupliziert. Das erhöht Drift-Risiko und Wartungsaufwand.

## Repro-Schritte
1. `cd /home/sascha/Dokumente/bots`
2. Vergleiche die Funktionen `_build_file_logger`, `_is_timeout_error`, `_is_dns_error`, `_is_connection_error`, `_is_gateway_error`, `_describe_network_error` in beiden Dateien.

## Logs / Stacktrace
- Kein Stacktrace; strukturelles Wartbarkeitsproblem.

## Impact
- Bugfixes/Security-Fixes müssen doppelt gepflegt werden.
- Divergenz zwischen Control-Bots wahrscheinlich.

## Fix-Idee
- Gemeinsames Modul für Netzwerkfehler-Klassifikation und File-Logger-Builder einführen.
- Beide Control-Bots auf das Shared-Modul umstellen.
