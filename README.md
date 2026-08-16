# ha-server-updater

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)
[![Validate](https://github.com/Internerd/ha-server-updater/actions/workflows/validate.yml/badge.svg)](https://github.com/Internerd/ha-server-updater/actions/workflows/validate.yml)
[![GitHub Release](https://img.shields.io/github/v/release/Internerd/ha-server-updater?include_prereleases)](https://github.com/Internerd/ha-server-updater/releases)
[![License](https://img.shields.io/github/license/Internerd/ha-server-updater)](LICENSE)

Home Assistant Custom Integration, die Linux-Server (Debian, Ubuntu, Proxmox VE)
per SSH auf verfügbare Paket-Updates prüft und Updates/Reboots direkt aus
Home Assistant heraus anstoßen kann.

Es lassen sich beliebig viele Server konfigurieren, jeder als eigener
"Config Entry" mit eigenem Gerät und eigenen Entitäten.

## Inhalt

- [Bereitgestellte Entitäten](#bereitgestellte-entitäten-pro-server)
- [Docker-Container-Updates](#docker-container-updates)
- [Installation](#installation)
- [Einrichtung](#einrichtung)
- [Sudo-Konfiguration](#sudo-konfiguration)
- [Voraussetzungen auf dem Zielserver](#voraussetzungen-auf-dem-zielserver)
- [Sicherheitshinweise](#sicherheitshinweise)
- [Technischer Hintergrund](#technischer-hintergrund)
- [Troubleshooting](#troubleshooting)
- [Hinweis zu Add-on vs. Integration](#hinweis-zu-add-on-vs-integration)
- [Mitwirken](#mitwirken)
- [Rechtliches & KI-Hinweis](#rechtliches--ki-hinweis)

## Bereitgestellte Entitäten (pro Server)

| Entität | Typ | Beschreibung |
|---|---|---|
| `binary_sensor.<server>_updates_available` | binary_sensor | An, wenn Paket-Updates verfügbar sind. Attribute: `update_count`, `packages`, `os_name` |
| `binary_sensor.<server>_reboot_required` | binary_sensor | An, wenn der Server einen Neustart benötigt (`/var/run/reboot-required`). Attribut: `reboot_required_packages` |
| `button.<server>_update` | button | Führt `apt-get update && apt-get dist-upgrade` auf dem Server aus |
| `button.<server>_update_and_reboot` | button | Wie oben, anschließend Neustart des Servers |
| `button.<server>_rescan_containers` | button | Durchsucht den Server erneut nach laufenden Docker-Containern (siehe unten) |
| `update.<container>` | update | Pro erkennbarem Docker-Container: zeigt an, ob ein neueres Image verfügbar ist; bei Docker-Compose-Containern zusätzlich ein Installieren-Button (siehe unten) |

Nach einem Tastendruck laufen Update/Reboot im Hintergrund; danach werden die
Sensoren automatisch aktualisiert.

## Docker-Container-Updates

Läuft auf dem Server Docker, inventarisiert die Integration zusätzlich die
laufenden Container und prüft, ob für deren Image eine neuere Version
vorliegt – z. B. für einen Caddy-Container oder selbst gebaute Images aus
GitHub-Actions-Workflows (GHCR).

**Wie die Erkennung funktioniert:**

1. Beim ersten Laden der Integration (und über den Button
   `Container neu inventarisieren`) wird per SSH `docker ps`/`docker inspect`
   ausgeführt, um laufende Container und deren Image-Referenz zu ermitteln.
   Dieser Scan läuft **nicht** bei jeder regulären Abfrage, sondern nur beim
   Start bzw. auf Knopfdruck – damit neu deployte Container erkannt werden,
   ohne dass jedes Mal der ganze Server durchsucht wird.
2. Für jeden gefundenen Container fragt Home Assistant direkt (nicht über
   SSH) beim jeweiligen Container-Registry (Docker Hub, GHCR oder eine
   andere Registry, die anonyme Docker-Registry-v2-Abfragen erlaubt) den
   aktuellen Manifest-Digest des verwendeten Tags ab und vergleicht ihn mit
   dem lokal laufenden Image. Das passiert bei jeder regulären Abfrage
   (siehe Abfrageintervall in den Optionen).
3. **Nur wenn dieser Abgleich zuverlässig möglich ist**, wird eine
   `update`-Entität für den Container angelegt. Kein Update-Entity entsteht
   für: Container, deren Image auf einen exakten Digest statt einen Tag
   fixiert ist; Images aus privaten/authentifizierungspflichtigen
   Registries; oder lokal gebaute Images ohne bekannte Registry-Herkunft.
   Verschwindet ein Container beim nächsten Rescan, wird seine Entität
   automatisch entfernt.

Jede `update`-Entität zeigt die (verkürzten) Digests als installierte/neueste
Version und – sofern das Image das OCI-Label
`org.opencontainers.image.source` trägt (bei GitHub-Actions-Builds meist
automatisch gesetzt) – einen Link zum Quell-Repository.

**"Installieren"-Button – nur für Docker-Compose-verwaltete Container**: Setzt
Docker Compose (v2, `docker compose ...`) den Container auf, hinterlegt es
automatisch die Labels `com.docker.compose.service` und
`com.docker.compose.project.config_files` (Pfad zur `docker-compose.yml`).
Sind diese vorhanden **und** existiert die Compose-Datei beim letzten Scan
noch am hinterlegten Pfad auf dem Server, bekommt die `update`-Entität einen
Installieren-Button. Ein Klick führt aus:

```bash
docker compose -f <config_files> [--project-directory <working_dir>] pull <service>
docker compose -f <config_files> [--project-directory <working_dir>] up -d <service>
```

Das erstellt den Container aus der originalen Compose-Datei neu und
übernimmt damit automatisch Volumes, Netzwerke und Umgebungsvariablen
korrekt – im Gegensatz zu einer nachgebauten `docker run`-Zeile. Nach dem
Update wird automatisch neu inventarisiert. Für Container ohne diese Labels
(reines `docker run`, oder Compose v1 mit dem separaten `docker-compose`-
Binary) bleibt die Entität rein informativ, wie oben beschrieben – ein
automatisiertes Neuerstellen ohne bekannte, vollständige Original-
Konfiguration ist bewusst nicht implementiert, das Risiko einer falsch
rekonstruierten Konfiguration (fehlende Volumes/Netzwerke/Env) wäre zu hoch,
insbesondere bei zentralen Diensten wie einem Reverse Proxy.

**Voraussetzungen**: `docker`-CLI-Zugriff über denselben SSH-Nutzer (bzw.
`sudo`, falls aktiv) wie für die Paket-Updates, sowie eine
Internetverbindung *von Home Assistant aus* zur jeweiligen Registry (nicht
vom Zielserver). Für den Installieren-Button zusätzlich: Docker Compose v2
auf dem Server, und die Compose-Datei muss unter dem Pfad erreichbar sein,
den Docker sich beim Erstellen des Containers gemerkt hat.

## Installation

### Über HACS (empfohlen)

Dieses Repository ist (noch) nicht im HACS-Standardstore gelistet und muss
einmalig als benutzerdefiniertes Repository hinzugefügt werden:

[![Als benutzerdefiniertes Repository zu HACS hinzufügen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Internerd&repository=ha-server-updater&category=integration)

Oder manuell:

1. HACS → Integrationen → Menü (⋮) → "Benutzerdefinierte Repositories"
2. URL `https://github.com/Internerd/ha-server-updater` als Typ
   "Integration" hinzufügen
3. "Server Updater" installieren und Home Assistant neu starten

HACS zeigt neue Versionen zukünftiger [GitHub Releases](https://github.com/Internerd/ha-server-updater/releases)
automatisch als Update an (siehe [CHANGELOG.md](CHANGELOG.md)).

### Manuell

`custom_components/server_updater` in das `custom_components`-Verzeichnis
der Home-Assistant-Konfiguration kopieren und Home Assistant neu starten.
Bei manueller Installation übernimmt HACS keine Update-Benachrichtigungen.

## Einrichtung

Einstellungen → Geräte & Dienste → Integration hinzufügen → "Server Updater".
Alle Angaben stehen auf einer einzigen Seite, damit sich bei einem
fehlgeschlagenen Verbindungstest einzelne Felder korrigieren und erneut
absenden lassen, ohne von vorne anfangen zu müssen:

- **Name, Host/IP, SSH-Port, Benutzername**
- **Passwort** und/oder **privater SSH-Schlüssel** (mindestens eines von
  beiden ist erforderlich; ist ein Schlüssel angegeben, wird dieser
  bevorzugt)
- **Sudo**: Ob Befehle mit `sudo` ausgeführt werden sollen (für Updates und
  Reboot i. d. R. notwendig, außer beim Login als `root`), und optional ein
  Sudo-Passwort

**Privaten SSH-Schlüssel einfügen**: Auf dem Rechner, der den Schlüssel
besitzt, den Inhalt der privaten Schlüsseldatei ausgeben, z. B.:

```bash
cat ~/.ssh/id_ed25519
```

und den **kompletten Text** in das Feld "Privater Schlüssel" einfügen —
inklusive der ersten Zeile `-----BEGIN ... PRIVATE KEY-----` und der letzten
Zeile `-----END ... PRIVATE KEY-----`. Nicht die `.pub`-Datei verwenden, das
ist der öffentliche Schlüssel. Ist der Schlüssel mit einer Passphrase
geschützt, diese im Feld "Passphrase" eintragen, sonst leer lassen. Der
öffentliche Gegenpart muss bereits in `~/.ssh/authorized_keys` des
Zielservers für den angegebenen Benutzer hinterlegt sein.

Schlägt der Verbindungstest fehl, zeigt das Formular die konkrete
Fehlermeldung (z. B. Timeout, falsches Passwort, ungültiger Schlüssel) direkt
an und behält alle bereits eingegebenen Werte bei.

Für jeden weiteren Server die Integration erneut über "Integration hinzufügen"
einrichten.

Über die Optionen des jeweiligen Config Entry lassen sich das Abfrageintervall
(Standard: 6 Stunden) und ob vor jeder Prüfung `apt-get update` ausgeführt
werden soll, anpassen.

## Sudo-Konfiguration

Für die Update- und Reboot-Buttons wird auf dem Zielserver in der Regel
Root-Zugriff benötigt. Zwei Möglichkeiten:

**Option A – Sudo-Passwort im Config Flow hinterlegen**
Passwort wird bei jedem privilegierten Befehl per `sudo -S` übergeben.

**Option B – NOPASSWD in den sudoers (empfohlen)**
Sudo-Passwort-Feld leer lassen und stattdessen auf dem Server einen engen
sudoers-Eintrag anlegen, z. B. via `visudo -f /etc/sudoers.d/ha-server-updater`:

```
ha_updater ALL=(root) NOPASSWD: /usr/bin/apt-get, /usr/sbin/reboot
```

`ha_updater` durch den in Home Assistant konfigurierten Benutzernamen
ersetzen. So bekommt der SSH-Nutzer ausschließlich für `apt-get` und `reboot`
passwortlosen Root-Zugriff, statt vollen Sudo-Zugriff.

## Voraussetzungen auf dem Zielserver

- Debian, Ubuntu oder Proxmox VE (apt-basiert)
- Erreichbarer SSH-Server
- `sudo` installiert, sofern `use_sudo` aktiv ist
- Optional für Docker-Container-Updates: Docker installiert und per SSH
  erreichbar (siehe [Docker-Container-Updates](#docker-container-updates))

## Sicherheitshinweise

- Ein privater SSH-Schlüssel mit Passphrase ist einem reinen Passwort vorzuziehen.
- Den sudoers-Eintrag möglichst eng fassen (siehe oben), statt uneingeschränktes
  `NOPASSWD: ALL` zu vergeben.
- Zugangsdaten werden von Home Assistant wie üblich in der Konfigurations-
  datenbank gespeichert.

## Technischer Hintergrund

Die Integration verbindet sich bei jeder Prüfung bzw. jedem Update kurzzeitig
per SSH ([asyncssh](https://asyncssh.readthedocs.io/)) mit dem Zielserver,
es wird keine dauerhafte Verbindung gehalten. Da Debian, Ubuntu und
Proxmox VE alle auf `apt` basieren, kommt derselbe Befehlssatz
(`apt list --upgradable`, `apt-get dist-upgrade`, `/var/run/reboot-required`)
für alle drei Systeme zum Einsatz.

## Troubleshooting

**"Verbindung fehlgeschlagen" im Config Flow**
- Host/Port/Firewall prüfen (`ssh <user>@<host> -p <port>` manuell testen)
- Bei Schlüssel-Auth: Inhalt der *privaten* Schlüsseldatei einfügen (nicht
  die `.pub`-Datei), Format muss von den OpenSSH- bzw. PEM-Parsern von
  [asyncssh](https://asyncssh.readthedocs.io/) unterstützt werden
- Bei aktivem Sudo: ohne hinterlegtes Sudo-Passwort muss auf dem Server ein
  passender `NOPASSWD`-Sudoers-Eintrag existieren (siehe oben), sonst schlägt
  der Sudo-Test in `async_test_connection` fehl

**`apt-get update` schlägt fehl / Paketliste wirkt veraltet**
- Wird nur als Warnung geloggt, die Prüfung läuft mit der vorhandenen
  Paketliste weiter. Meist fehlende Root-Rechte (siehe Sudo-Konfiguration)
  oder kein Internetzugang auf dem Zielserver
- Lässt sich in den Optionen des Config Entry deaktivieren
  ("Paketliste vor jeder Prüfung aktualisieren")

**Update-/Reboot-Button reagiert nicht sichtbar**
- Der Vorgang läuft bewusst im Hintergrund (kann bei vielen Updates mehrere
  Minuten dauern); Fortschritt ist an der `updates_available`-Entität nach
  Abschluss sichtbar. Details stehen im Home-Assistant-Log
  (Einstellungen → System → Protokolle, Filter `server_updater`)
- Während ein Update läuft, ist die Entität `unavailable`, um Doppel-Presses
  zu verhindern

**Entitäten bleiben nach einem Neustart-Button `unavailable`**
- Erwartet: die SSH-Verbindung bricht durch den Reboot ab. Nach dem nächsten
  regulären Poll-Intervall (oder manuellem "Neu laden" der Integration)
  aktualisieren sich die Sensoren wieder, sobald der Server wieder erreichbar
  ist

**Keine `update`-Entitäten für Docker-Container, obwohl welche laufen**
- Erwartet, wenn die Registry nicht anonym abfragbar ist (privates Image),
  das Image auf einen Digest statt einen Tag fixiert ist, oder es lokal
  ohne Registry-Herkunft gebaut wurde – siehe
  [Docker-Container-Updates](#docker-container-updates)
- `docker ps`/`docker inspect` auf dem Server manuell mit dem konfigurierten
  SSH-Nutzer (bzw. `sudo docker ...`) testen
- Nach dem Deployen neuer Container den Button
  `Container neu inventarisieren` drücken

**`update`-Entität hat keinen Installieren-Button**
- Erwartet für Container ohne Docker Compose (v2) oder mit veraltetem
  Compose-Datei-Pfad (z. B. nach Verschieben des Stacks) – nach einem Fix
  einmal `Container neu inventarisieren` drücken, damit der Pfad neu
  geprüft wird
- `docker compose -f <pfad> config` mit dem konfigurierten SSH-Nutzer manuell
  testen, um Rechte-/Pfadprobleme einzugrenzen

**Installieren schlägt fehl**
- Fehlermeldung im Home-Assistant-Log (`server_updater`) enthält die Ausgabe
  von `docker compose pull`/`up -d`; meist Rechte- oder Netzwerkprobleme auf
  dem Server selbst

Weitere Probleme bitte als [Issue](https://github.com/Internerd/ha-server-updater/issues/new/choose)
melden.

## Hinweis zu Add-on vs. Integration

Diese Lösung ist als HA-Integration umgesetzt, da sie so am direktesten
Entitäten bereitstellt, mehrere Server über normale Config Entries
unterstützt und ohne zusätzlichen Container auskommt. Ein separates
Supervisor-Add-on wäre für denselben Funktionsumfang nur zusätzliche,
redundante Infrastruktur (eigener Container, eigene Anbindung der Entitäten
via MQTT/REST) – bei Bedarf kann das aber ergänzt werden.

## Mitwirken

Beiträge sind willkommen – siehe [CONTRIBUTING.md](CONTRIBUTING.md) für
Entwicklungs-Setup, Tests und den Release-Prozess. Änderungen werden in
[CHANGELOG.md](CHANGELOG.md) festgehalten. Sicherheitsrelevante Meldungen
bitte gemäß [SECURITY.md](SECURITY.md) einreichen.

## Rechtliches & KI-Hinweis

Dieses Projekt steht unter der [MIT-Lizenz](LICENSE). Es wurde mit
Unterstützung eines KI-Assistenten (Claude Code) entwickelt, ist ein
unabhängiges Community-Projekt ohne Zugehörigkeit zu Home Assistant,
Debian, Ubuntu oder Proxmox, und übernimmt keine Gewährleistung für
Aktionen auf deinen Servern. Details, Lizenzen der Abhängigkeiten
(u. a. [asyncssh](https://github.com/ronf/asyncssh)) und der vollständige
Haftungsausschluss stehen in [NOTICE.md](NOTICE.md).
