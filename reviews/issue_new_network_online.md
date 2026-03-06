## Problem
Die systemd-Units der Bots verwenden nur `After=network.target`. Da die Bots externe APIs benötigen, kann der Start vor vollständiger Netzwerkverfügbarkeit stattfinden.

## Repro-Schritte
1. Starte den Host neu.
2. Prüfe `journalctl -u <bot-service>` direkt nach Boot.
3. Dienste starten potenziell vor `network-online.target` und erzeugen frühe Verbindungsfehler/Restarts.

## Logs/Stacktrace
Typischer Betriebseindruck:
- frühe Verbindungsfehler unmittelbar nach Boot
- unnötige Restarts trotz später stabiler Netzverbindung

## Impact
- Instabiler Start nach Reboot.
- Vermeidbare Fehler und Log-Spam.

## Fix-Idee
- In allen netzabhängigen Units ergänzen:
  - `Wants=network-online.target`
  - `After=network-online.target`

## Acceptance Criteria
- Service-Units referenzieren `network-online.target` konsistent.
- `systemd-analyze verify services/*.service` bleibt erfolgreich.
