# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionsnummern folgen [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.4.1] - 2026-10-12

### Geändert

- Scheitert `apt-get dist-upgrade` am Herunterladen einzelner Pakete (z. B.
  `404 Not Found`, weil der Mirror gerade synchronisiert), werden die
  Paketlisten nach 30 s erneut und ohne HTTP-Cache geladen und das Upgrade
  einmal wiederholt. Da apt bei fehlgeschlagenem Download nichts installiert,
  ist das gefahrlos. Scheitert auch der zweite Versuch, gibt es eine
  verständliche Fehlermeldung statt der reinen apt-Ausgabe.

## [0.4.0] - 2026-10-12

### Hinzugefügt

- `update`-Entität „Systempakete“ pro Server: Ausstehende Paket-Updates
  erscheinen unter Einstellungen → Updates neben Core, Add-ons und HACS.
  Installierte Version = OS-Stand (Proxmox VE aus `pveversion`, Debian aus
  `/etc/debian_version`, sonst `/etc/os-release`), neueste Version = OS-Stand
  plus Anzahl ausstehender Pakete. Versionshinweise listen alle Pakete mit
  alter → neuer Version; „Installieren“ führt `apt-get dist-upgrade` aus.
- Reparatur-Hinweis „Neustart erforderlich“ unter Einstellungen → System →
  Reparaturen, solange der Server `/var/run/reboot-required` meldet.

### Entfernt

- `binary_sensor.<server>_updates_available` (ersetzt durch die
  `update`-Entität „Systempakete“). Der verwaiste Registry-Eintrag wird beim
  Start automatisch entfernt.

## [0.3.1] - 2026-10-12

### Behoben

- Installieren-Button erschien nie: Die `com.docker.compose.*`-Labels wurden
  aus den Image-Labels statt aus den Container-Labels gelesen, wodurch kein
  Compose-Container als installierbar erkannt wurde. Jetzt werden Image- und
  Container-Labels zusammengeführt.
- Doppelte Container-Entitäten und -Geräte (`update.<name>_2`, `_3`, …):
  Entitäten waren an die Container-ID gebunden, die sich bei jedem
  Neuerstellen ändert. Sie sind jetzt an den Containernamen gebunden;
  verwaiste Entitäten/Geräte (auch aus älteren Versionen) werden nach jedem
  erfolgreichen Scan entfernt, bevor neue angelegt werden.
- Für Container, deren Update-Stand nicht ermittelbar ist (z. B. lokal
  gebaute Images), wurde entgegen der Dokumentation trotzdem eine dauerhaft
  nicht verfügbare Entität angelegt. Entitäten entstehen jetzt erst, sobald
  der Abgleich mit der Registry einmal gelungen ist.

### Geändert

- Abhängigkeit `asyncssh` nicht mehr exakt auf 2.23.1 fixiert (`>=2.23.1`),
  da Home Assistant inzwischen selbst 2.24.0 mitbringt und der exakte Pin
  damit kollidiert (hassfest-Fehler).
- `update`-Entitäten zeigen während einer laufenden Installation den
  Fortschrittszustand (`in_progress`) an.

## [0.3.0] - 2026-08-13

### Hinzugefügt

- Installieren-Button für Docker-Container-`update`-Entitäten, sofern der
  Container von Docker Compose (v2) verwaltet wird (erkannt über die
  `com.docker.compose.*`-Labels) und die zugehörige Compose-Datei beim
  letzten Scan noch am hinterlegten Pfad existierte. Führt
  `docker compose pull` + `up -d` für genau diesen Service aus, wodurch
  Volumes/Netzwerke/Env korrekt aus der Original-Compose-Datei übernommen
  werden. Nach dem Update wird automatisch neu inventarisiert. Container
  ohne diese Labels bleiben wie zuvor rein informativ.

## [0.2.0] - 2026-08-13

### Hinzugefügt

- Docker-Container-Erkennung und Update-Anzeige pro Server: Ein neuer
  Button `Container neu inventarisieren` listet laufende Docker-Container
  per SSH auf; für jeden Container, dessen Image-Update sich zuverlässig
  über die jeweilige Registry (Docker Hub, GHCR o. Ä.) bestimmen lässt,
  wird automatisch eine `update`-Entität angelegt bzw. bei Verschwinden des
  Containers wieder entfernt. Die reguläre Abfrage prüft nur bekannte
  Container erneut (per Digest-Abgleich, ohne SSH), das teure Inventar-Scan
  passiert nur beim Start der Integration und auf Knopfdruck. Bewusst ohne
  automatisches Installieren (siehe README).

## [0.1.2] - 2026-08-13

### Behoben

- SSH-Port wurde vom `NumberSelector` des Config Flow als Fließkommazahl
  übermittelt, was `asyncssh` mit "Int or String expected" quittierte.
  Der Port wird jetzt zuverlässig als Ganzzahl übernommen.
- Kopierte private Schlüssel schlugen mit "Invalid private key" fehl, wenn
  sie CRLF-Zeilenumbrüche oder Leerzeichen am Zeilenende enthielten (z. B.
  durch Copy-Paste aus manchen Browsern/Editoren). Der Schlüsseltext wird
  vor dem Parsen normalisiert.

### Geändert

- Config Flow auf ein einzelnes Formular reduziert (statt vier
  Schritten ohne Zurück-Möglichkeit). Bei fehlgeschlagenem
  Verbindungstest bleiben alle eingegebenen Werte erhalten und lassen sich
  direkt korrigieren und erneut absenden.
- Fehlermeldungen bei fehlgeschlagener Verbindung zeigen jetzt die konkrete
  Ursache (z. B. Timeout, falsches Passwort, ungültiger Schlüssel) statt
  eines generischen "Verbindung fehlgeschlagen".
- Beschreibungstext im Config Flow erklärt jetzt im Detail, wie ein
  privater SSH-Schlüssel korrekt eingefügt wird.

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

[Unreleased]: https://github.com/Internerd/ha-server-updater/compare/v0.4.1...HEAD
[0.4.1]: https://github.com/Internerd/ha-server-updater/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Internerd/ha-server-updater/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Internerd/ha-server-updater/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Internerd/ha-server-updater/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Internerd/ha-server-updater/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/Internerd/ha-server-updater/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Internerd/ha-server-updater/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Internerd/ha-server-updater/releases/tag/v0.1.0
