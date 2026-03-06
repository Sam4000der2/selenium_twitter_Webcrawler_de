## Problem
Die README enthält veraltete bzw. nicht mehr wirksame Konfig-Hinweise (`delete_temp_files`, Legacy-Konstanten wie `filename`/`RULES_FILE`). Diese Optionen sind im aktuellen Codepfad nicht relevant.

## Repro-Schritte
1. Lies die README-Konfigabschnitte.
2. Suche die genannten Optionen im Code (`rg`).
3. Es gibt keine oder nur irrelevante Treffer außerhalb der README.

## Logs/Stacktrace
Kein Crash; es handelt sich um Doku-Drift und Betriebsirreführung.

## Impact
- Betreiber passen falsche Einstellungen an.
- Zeitverlust und höhere Fehlkonfigurationswahrscheinlichkeit.

## Fix-Idee
- README auf tatsächlich genutzte Konfigurationsschlüssel reduzieren.
- Legacy-Hinweise entfernen oder als historisch kennzeichnen.

## Acceptance Criteria
- README referenziert nur aktive, im Code verwendete Konfig-Optionen.
- Konfig-Abschnitt ist reproduzierbar und konsistent mit Runtime.
