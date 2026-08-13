# Security Policy

Diese Integration verarbeitet SSH-Zugangsdaten (Passwörter, private
Schlüssel, optional Sudo-Passwörter) und führt privilegierte Befehle auf
extern konfigurierten Servern aus. Sicherheitsrelevante Meldungen werden
entsprechend bevorzugt behandelt.

## Eine Schwachstelle melden

Bitte **keine** sicherheitsrelevanten Details (z. B. Wege zur
Rechteausweitung, Command-Injection-Vektoren) in einem öffentlichen Issue
posten. Stattdessen:

1. GitHub Security Advisory für dieses Repository nutzen
   ("Security" → "Report a vulnerability"), oder
2. Falls nicht verfügbar, ein Issue mit minimalen Details erstellen und um
   einen privaten Kontaktweg bitten

## Umfang

Relevant sind u. a.:

- Command-/Shell-Injection über Konfigurationswerte (Host, Benutzername,
  etc.) in die per SSH ausgeführten Befehle
- Unsichere Handhabung von Passwörtern/privaten Schlüsseln (Logging,
  Speicherung)
- Umgehung der Sudo-Einschränkungen

Nicht im Fokus: Schwachstellen in Home Assistant Core selbst oder in
Abhängigkeiten wie [asyncssh](https://github.com/ronf/asyncssh) – dafür
bitte die jeweiligen Upstream-Projekte kontaktieren.
