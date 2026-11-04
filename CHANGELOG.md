# Changelog

Alle nennenswerten Änderungen an diesem Projekt werden hier dokumentiert.
Das Format orientiert sich an [Keep a Changelog](https://keepachangelog.com/de/1.1.0/),
Versionsnummern folgen [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.8.0] - 2026-11-04

### Hinzugefügt

- **Neustart für Dienste, die nur ein Server-Neustart aktualisiert**: Meldet
  needrestart Dienste wie `dbus.service` oder `systemd-logind.service`, die
  bewusst nicht einzeln neu gestartet werden, gilt der Server als
  neustartbedürftig. `Neustart erforderlich` geht an (neues Attribut
  `services_requiring_reboot`), und unter *Reparaturen* erscheint die
  Meldung „Neustart erforderlich“, die den Server per Klick neu startet –
  wie bei Kernel-Updates. Die Wartungsaktion mit `reboot: if_required`
  berücksichtigt das ebenfalls.

### Geändert

- Sensor `Neu zu startende Dienste` und Button `Dienste neu starten`
  erscheinen jetzt von selbst, sobald needrestart auf dem Server installiert
  wird; bisher war dafür ein Neuladen der Integration nötig.
- README: Hinweis, dass needrestart auf Debian und Proxmox VE erst
  installiert werden muss (`apt install needrestart`).

## [0.7.2] - 2026-11-04

### Behoben

- Button `Dienste neu starten` ist nur noch verfügbar, wenn es Dienste
  gibt, die er auch neu starten darf. Stehen nur geschützte Dienste wie
  `dbus.service` oder `systemd-logind.service` aus (dafür ist ein Neustart
  des Servers nötig), führte ein Druck bisher zu einem Fehler im Log.
- Container-Geräte werden ab Home Assistant 2026.12 per `via_device_id` mit
  dem Server verknüpft statt über das veraltete `via_device` (entfällt mit
  2027.8). Ältere Versionen nutzen weiter `via_device`.
- Warnung „has an update listener and should use it for scheduling a
  reload“ beim Neu-Konfigurieren behoben: Optionsänderungen laden den
  Eintrag jetzt über `OptionsFlowWithReload` neu (ab HA 2025.8), ohne
  Update-Listener.

## [0.7.1] - 2026-11-04

### Behoben

- Paket-Updates schlugen mit `sudo: DEBIAN_FRONTEND=noninteractive: command
  not found` fehl, u. a. mit sudo-rs (Standard ab Ubuntu 25.10). Die
  Variable stand hinter `--` und wurde dadurch als Befehl ausgeführt; sie
  wird jetzt vor dem Befehl übergeben.
- Lehnt sudo das Setzen der Variable ab (z. B. eng gefasste sudoers-Regel
  nur für `/usr/bin/apt-get` ohne `SETENV`), wird das Update ohne sie
  ausgeführt statt abzubrechen. Bis dahin wurde nichts installiert.

## [0.7.0] - 2026-11-03

### Hinzugefügt

- **Neu konfigurieren**: Host/IP, Port, Name, Benutzer, Passwort,
  privater Schlüssel, Passphrase und sudo-Einstellungen lassen sich über
  „Neu konfigurieren“ ändern, ohne den Server zu löschen. Leere
  Passwort-/Schlüsselfelder behalten die gespeicherten Werte; die Verbindung
  wird vor dem Speichern getestet.
- **Erneute Authentifizierung**: Wird die Anmeldung abgelehnt oder hat sich
  der Hostschlüssel geändert, startet Home Assistant einen
  Reauth-Dialog, statt den Server nur als nicht verfügbar zu zeigen.

### Sicherheit

- **SSH-Hostschlüssel werden jetzt geprüft.** Bisher wurde jeder
  Hostschlüssel akzeptiert, wodurch ein Angreifer im Netzwerk
  (Man-in-the-Middle) Passwort und Sudo-Passwort hätte abgreifen können.
  Der Schlüssel wird beim ersten Verbinden gespeichert (bestehende Einträge
  beim ersten Abruf nach dem Update); ein geänderter Schlüssel muss mit
  angezeigtem Fingerabdruck ausdrücklich bestätigt werden, bevor
  Zugangsdaten gesendet werden.
- SSH-Verbindungen nutzen nur noch die in der Integration hinterlegten
  Zugangsdaten – keine Schlüssel, kein SSH-Agent und keine `~/.ssh/config`
  des Home-Assistant-Hosts.
- Container-IDs, Image-Namen, Compose-Dateien/-Verzeichnisse und
  Compose-Dienstnamen (stammen aus Labels auf dem Zielserver) werden mit
  `shlex.quote` maskiert, statt in einfache Anführungszeichen gesetzt bzw.
  unmaskiert übernommen zu werden.
- Registry-Auth-Endpunkte (`WWW-Authenticate: realm`) werden nur noch über
  HTTPS angefragt.

### Behoben

- Die Prüfung, ob Compose-Dateien existieren, lief bei mehreren Dateien
  nur für die erste mit sudo.
- Optionsänderungen laden den Eintrag weiterhin neu, reine Datenänderungen
  (z. B. das Speichern des Hostschlüssels) nicht mehr.

## [0.6.0] - 2026-10-30

### Hinzugefügt

- **Neustart-Überwachung**: Nach jedem Neustart-Befehl wird nach ca. drei
  Minuten und danach minütlich (bis 15 Minuten) geprüft, ob der Server
  erreichbar ist, einen neuen Startzeitpunkt hat und – auf Proxmox VE – alle
  Autostart-Gäste laufen. Andernfalls gibt es eine Fehlermeldung mit Grund.
- `binary_sensor.<server>_reachable` (Konnektivität, bleibt auch bei
  ausgefallenem Server verfügbar) und `sensor.<server>_last_boot`
  (letzter Start).
- **Proxmox VE**: `binary_sensor.<server>_autostart_guests_stopped` meldet
  Gäste mit „Beim Booten starten“, die nicht laufen (per `pvesh`). Das
  geordnete Herunterfahren der Gäste beim Neustart übernimmt weiterhin
  Proxmox VE selbst (`pve-guests`).
- **needrestart**: `sensor.<server>_services_to_restart` und Button
  `Dienste neu starten` für Dienste mit veralteten Bibliotheken – ohne
  Neustart des Servers. Kritische Dienste (dbus, systemd-*, getty, docker,
  containerd, pve-guests, lxc, qemu-server, pve-ha-*) werden dabei
  ausgelassen.
- **Docker**: Schalter `Läuft` pro Container (`docker start`/`stop`, mit
  Attributen `state`/`status`) und Server-Button `Ungenutzte Images
  aufräumen` (`docker image prune -f`). Gestoppte Container mit
  Neustart-Richtlinie (oder aus Home Assistant gestoppte) bleiben jetzt im
  Inventar.
- **Wartungsaktion** `server_updater.run_maintenance` für zeitgesteuerte
  Automationen: Paket-Updates, optional Container-Updates, Images aufräumen,
  Dienste neu starten, Neustart nie/bei Bedarf/immer – für alle oder
  ausgewählte Server, mit optionaler Zusammenfassung als Antwort.
- **Ereignisse & Benachrichtigungen**: Jede Aktion feuert
  `server_updater_event` mit Ergebnis; Fehler erzeugen zusätzlich eine
  Benachrichtigung in Home Assistant, die bei späterem Erfolg wieder
  verschwindet (in den Optionen abschaltbar).
- Automatisierte Tests (`tests/`) mit simuliertem Server, laufen in der CI.

### Geändert

- Der Container-Laufzustand wird bei jeder regulären Abfrage mit einem
  `docker ps -a` per SSH gelesen (bisher nur Registry-Abfragen).
- Update-Fehler über die Buttons werden nicht mehr nur geloggt, sondern auch
  gemeldet (Ereignis/Benachrichtigung). Ein zweiter Update-Vorgang während
  eines laufenden wird mit Fehlermeldung abgelehnt statt still ignoriert.

## [0.5.0] - 2026-10-30

### Hinzugefügt

- Button `Neu starten` pro Server (`button.<server>_reboot`): startet den
  Server neu, ohne vorher Updates einzuspielen – zusätzlich zu
  `Aktualisieren und neu starten`.
- Button `Neu starten` pro Docker-Container (`button.<container>_restart`):
  führt `docker restart` aus. Gibt es für jeden laufenden Container, auch
  für solche ohne `update`-Entität (z. B. lokal gebaute Images); diese
  bekommen dafür jetzt ebenfalls ein eigenes Gerät.
- Reparatur-Hinweis „Neustart erforderlich“ ist jetzt direkt behebbar:
  „Beheben“ unter Einstellungen → System → Reparaturen startet den Server
  nach Bestätigung neu.
- Kernel-Updates werden als Neustart-Grund erkannt, wenn unter `/boot` ein
  neuerer Kernel (gleicher Variante, z. B. `-pve`) liegt als der laufende.
  Dadurch erscheint der Reparatur-Hinweis jetzt auch auf Proxmox VE, das bei
  Kernel-Updates keine `/var/run/reboot-required` anlegt. Neue Attribute
  `running_kernel` und `newest_kernel` am `reboot_required`-Sensor.

### Geändert

- Nach einem Neustart (Button, `Aktualisieren und neu starten` oder
  Reparatur-Hinweis) wird der Server nach ca. drei Minuten erneut geprüft,
  statt sofort – die sofortige Abfrage lief bisher gegen den gerade
  herunterfahrenden Server.

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

[Unreleased]: https://github.com/Internerd/ha-server-updater/compare/v0.8.0...HEAD
[0.8.0]: https://github.com/Internerd/ha-server-updater/compare/v0.7.2...v0.8.0
[0.7.2]: https://github.com/Internerd/ha-server-updater/compare/v0.7.1...v0.7.2
[0.7.1]: https://github.com/Internerd/ha-server-updater/compare/v0.7.0...v0.7.1
[0.7.0]: https://github.com/Internerd/ha-server-updater/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/Internerd/ha-server-updater/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/Internerd/ha-server-updater/compare/v0.4.1...v0.5.0
[0.4.1]: https://github.com/Internerd/ha-server-updater/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/Internerd/ha-server-updater/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/Internerd/ha-server-updater/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/Internerd/ha-server-updater/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/Internerd/ha-server-updater/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/Internerd/ha-server-updater/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/Internerd/ha-server-updater/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/Internerd/ha-server-updater/releases/tag/v0.1.0
