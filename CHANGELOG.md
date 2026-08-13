# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionsnummern folgen [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.1.1] - 2026-08-13

### Behoben

- Config Flow: Der Verbindungstest schlug beim Hinzufügen jedes Servers
  immer mit „Nicht verbunden" fehl, unabhängig von den Zugangsdaten, weil
  die SSH-Verbindung vor dem Test nie tatsächlich aufgebaut wurde.

## [0.1.0] - 2026-08-13

### Hinzugefügt

- Erste Version der Server-Updater-Integration
- Config Flow für beliebig viele Server (Passwort- oder Key-Auth, Sudo-Optionen)
- `binary_sensor` für verfügbare Updates und ausstehenden Neustart
- `button` zum Aktualisieren sowie Aktualisieren + Neustarten
- HACS-Kompatibilität (`hacs.json`, versioniertes `manifest.json`)

[Unreleased]: https://github.com/Internerd/ha-server-updater/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/Internerd/ha-server-updater/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Internerd/ha-server-updater/releases/tag/v0.1.0
