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
- [Neustart-Überwachung, Ereignisse & Benachrichtigungen](#neustart-überwachung-ereignisse--benachrichtigungen)
- [Dienste neu starten (needrestart)](#dienste-neu-starten-needrestart)
- [Proxmox VE: Gäste mit Autostart](#proxmox-ve-gäste-mit-autostart)
- [Wartungsaktion für Automationen](#wartungsaktion-für-automationen)
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
| `update.<server>_system_packages` | update | Ausstehende Paket-Updates des Servers – erscheint unter **Einstellungen → Updates** neben Core, Add-ons und HACS (siehe unten) |
| `binary_sensor.<server>_reboot_required` | binary_sensor | An, wenn der Server einen Neustart benötigt (`/var/run/reboot-required` oder neuerer Kernel installiert als aktiv). Attribute: `reboot_required_packages`, `running_kernel`, `newest_kernel` |
| `button.<server>_update` | button | Führt `apt-get update && apt-get dist-upgrade` auf dem Server aus |
| `button.<server>_update_and_reboot` | button | Wie oben, anschließend Neustart des Servers |
| `button.<server>_reboot` | button | Startet den Server neu, **ohne** vorher Updates einzuspielen |
| `binary_sensor.<server>_reachable` | binary_sensor | Ob die letzte Abfrage den Server per SSH erreicht hat. Bleibt auch bei ausgefallenem Server verfügbar (dann „aus“). Attribut: `rebooting` |
| `sensor.<server>_last_boot` | sensor | Zeitpunkt des letzten Starts (aus `/proc/stat`) |
| `sensor.<server>_services_to_restart` | sensor | Nur mit `needrestart`: Anzahl Dienste mit veralteten Bibliotheken. Attribute: `services`, `excluded_services` |
| `button.<server>_restart_services` | button | Nur mit `needrestart`: startet diese Dienste neu (siehe unten) |
| `binary_sensor.<server>_autostart_guests_stopped` | binary_sensor | Nur Proxmox VE: an, solange Gäste mit „Beim Booten starten“ nicht laufen. Attribut: `guests` |
| `button.<server>_rescan_containers` | button | Durchsucht den Server erneut nach Docker-Containern (siehe unten) |
| `button.<server>_prune_unused_images` | button | Entfernt ungenutzte (dangling) Docker-Images (`docker image prune -f`) |
| `button.<container>_restart` | button | Pro Docker-Container: startet den Container neu (`docker restart`) |
| `switch.<container>_running` | switch | Pro Docker-Container: an, solange er läuft; Schalten führt `docker start`/`docker stop` aus. Attribute: `state`, `status` |
| `update.<container>` | update | Pro erkennbarem Docker-Container: zeigt an, ob ein neueres Image verfügbar ist; bei Docker-Compose-Containern zusätzlich ein Installieren-Button (siehe unten) |

Nach einem Tastendruck laufen Update/Reboot im Hintergrund; danach werden die
Entitäten automatisch aktualisiert.

## Systempakete in der Home-Assistant-Update-Übersicht

Pro Server gibt es eine `update`-Entität „Systempakete“. Dadurch tauchen
ausstehende Paket-Updates dort auf, wo Home Assistant auch Core-, Add-on-,
HACS- und Firmware-Updates anzeigt (Einstellungen → Updates, inkl. Zähler am
Einstellungen-Symbol).

- **Installierte Version**: Stand des Betriebssystems – bei Proxmox VE die
  Version aus `pveversion` (z. B. `Proxmox VE 9.0.10`), bei Debian die
  Point-Release aus `/etc/debian_version` (z. B. `12.7`), bei Ubuntu aus
  `/etc/os-release` (z. B. `24.04.1 LTS`).
- **Neueste Version**: dieselbe Angabe plus Anzahl ausstehender Pakete, z. B.
  `12.7 (+5 Pakete)`. Die Entität ist damit genau dann „an“, wenn Updates
  anstehen. „Überspringen“ gilt für genau diesen Stand – kommen weitere
  Pakete hinzu, wird das Update wieder angeboten.
- **Versionshinweise**: Tabelle aller Pakete mit installierter und neuer
  Version (aus `apt list --upgradable`).
- **Installieren**: führt `apt-get update && apt-get dist-upgrade` aus (ohne
  Neustart, Fortschritt wird angezeigt). Für Update mit anschließendem
  Neustart weiterhin den Button `Aktualisieren und neu starten` verwenden.
  Schlägt der Download einzelner Pakete fehl (z. B. `404 Not Found`, weil
  der Mirror gerade synchronisiert), werden die Paketlisten nach 30 s ohne
  HTTP-Cache neu geladen und das Upgrade einmal wiederholt.

**Neustart erforderlich**: Benötigt der Server einen Neustart, legt die
Integration zusätzlich einen Hinweis unter **Einstellungen → System →
Reparaturen** an (mit dem Grund). Erkannt wird das auf zwei Wegen:

- `/var/run/reboot-required` existiert (Ubuntu, Debian mit
  `update-notifier-common`/`unattended-upgrades`) – der Hinweis nennt dann
  die auslösenden Pakete aus `/var/run/reboot-required.pkgs`.
- Unter `/boot` ist ein neuerer Kernel installiert als der laufende
  (`uname -r`). Das deckt vor allem **Proxmox VE** ab, das bei
  Kernel-Updates keine `reboot-required`-Datei anlegt. Verglichen werden
  nur Kernel derselben Variante (z. B. `-pve`, `-amd64`), sodass ein
  zusätzlich installierter `-cloud-amd64`-Kernel keinen Hinweis auslöst.

Der Hinweis ist **direkt behebbar**: Über „Beheben“ öffnet sich ein
Bestätigungsdialog, der den Server neu startet (ohne vorher Updates
einzuspielen). Danach verschwindet der Hinweis; etwa drei Minuten später
wird der Server erneut geprüft, und nur wenn dann immer noch ein Neustart
nötig ist, erscheint er wieder. Ohne Klick verschwindet er bei der nächsten
Abfrage, sobald kein Neustart mehr nötig ist. Der
`binary_sensor.<server>_reboot_required` bleibt für Automationen
erhalten.

**Server neu starten**: Der Button `Neu starten` startet den Server sofort
neu, unabhängig von ausstehenden Updates – getrennt von `Aktualisieren und
neu starten`. Nach jedem Neustart (egal über welchen Weg) wird der Server
nach ca. drei Minuten automatisch erneut abgefragt, damit Sensoren und der
Reparatur-Hinweis den neuen Stand zeigen.

## Neustart-Überwachung, Ereignisse & Benachrichtigungen

**Neustart-Überwachung**: Nach jedem Neustart-Befehl (Button, „Aktualisieren
und neu starten“, Reparatur-Hinweis, Wartungsaktion) prüft die Integration
nach ca. drei Minuten und danach minütlich, bis zu 15 Minuten lang, ob der
Server zurück ist. Als erfolgreich gilt der Neustart erst, wenn

1. der Server per SSH erreichbar ist,
2. sein Startzeitpunkt neuer ist als vor dem Neustart (sonst wurde der
   Befehl z. B. von einem Inhibitor blockiert) und
3. auf Proxmox VE alle Gäste mit Autostart laufen (siehe unten).

Klappt das nicht innerhalb der 15 Minuten, gibt es ein Fehler-Ereignis und
eine Benachrichtigung mit dem konkreten Grund. Währenddessen hat
`binary_sensor.<server>_reachable` das Attribut `rebooting: true`.

Läuft Home Assistant selbst auf dem neu gestarteten Proxmox-Host, endet die
Überwachung mit dem Neustart von Home Assistant; die Entitäten aktualisieren
sich danach beim Laden der Integration.

**Ereignisse**: Jede Aktion feuert ein Home-Assistant-Ereignis
`server_updater_event`, z. B.:

```yaml
entry_id: 01J...
server: pve1
action: reboot        # update, reboot, services_restart, container_update,
                      # container_restart, container_stop, container_start,
                      # image_prune
success: false
error: "Gäste mit Autostart laufen nicht: VM 101 (dns)"
container: null       # bei Container-Aktionen der Containername
```

Je nach Aktion kommen weitere Felder hinzu (`packages`, `services`,
`excluded_services`, `reclaimed`). Beispiel-Automation für eine Push-Nachricht
bei Fehlern:

```yaml
triggers:
  - trigger: event
    event_type: server_updater_event
    event_data:
      success: false
actions:
  - action: notify.mobile_app_mein_handy
    data:
      title: "{{ trigger.event.data.server }}: {{ trigger.event.data.action }} fehlgeschlagen"
      message: "{{ trigger.event.data.error }}"
```

**Benachrichtigungen**: Schlägt eine Aktion fehl, erscheint zusätzlich eine
Benachrichtigung in Home Assistant (Glocke in der Seitenleiste). Gelingt
dieselbe Aktion später, verschwindet sie automatisch. Abschaltbar in den
Optionen des Servers („Bei Fehlern eine Benachrichtigung … anzeigen“); das
Ereignis wird trotzdem gefeuert.

## Dienste neu starten (needrestart)

Nach Bibliotheks-Updates (z. B. OpenSSL, libc) laufen Dienste oft noch mit
der alten Version im Speicher. Ein kompletter Neustart des Servers ist dafür
nicht nötig; es reicht, die betroffenen Dienste neu zu starten. Ist auf dem
Server [`needrestart`](https://github.com/liske/needrestart) installiert
(`apt install needrestart`, danach die Integration neu laden), ermittelt die
Integration bei jeder Abfrage per `needrestart -b` die betroffenen Dienste
und bietet dafür Sensor und Button an.

Der Button startet die Dienste per `systemctl restart` neu, **außer** solche,
deren Neustart Sitzungen beendet oder Gäste/Container betrifft: `dbus`,
`systemd-*`, `user@*`, `getty@*`, Display-Manager, `docker`/`containerd`
(würde alle Container stoppen), `pve-guests`, `lxc*`, `qemu-server`,
`pve-ha-*`. Diese stehen im Attribut `excluded_services` und erledigen sich
mit dem nächsten Neustart des Servers.

## Proxmox VE: Gäste mit Autostart

**Sauberes Herunterfahren**: Proxmox VE fährt bei einem Neustart des Hosts
alle Gäste selbst geordnet herunter (Dienst `pve-guests`, in umgekehrter
Start-Reihenfolge und mit den pro Gast eingestellten Shutdown-Timeouts unter
*Optionen → Start/Shutdown-Reihenfolge*). Die Integration nutzt dafür den
normalen `reboot` und greift nicht zusätzlich ein; wer längere Timeouts
braucht, stellt sie dort am Gast ein.

**Prüfung nach dem Start**: Auf Proxmox-VE-Hosts liest die Integration bei
jeder Abfrage per `pvesh` alle VMs und Container und meldet über
`binary_sensor.<server>_autostart_guests_stopped`, welche Gäste mit „Beim
Booten starten“ (`onboot: 1`) nicht laufen (Vorlagen ausgenommen). Die
Konfiguration wird nur für gestoppte Gäste abgefragt, damit die Abfrage auch
mit vielen Gästen schnell bleibt. Nach einem Neustart ist dieser Sensor Teil
der Neustart-Überwachung (siehe oben) – Gäste mit Startverzögerung haben
dafür bis zu 15 Minuten Zeit.

Benötigt `pvesh` mit Root-Rechten (siehe [Sudo-Konfiguration](#sudo-konfiguration)).

## Wartungsaktion für Automationen

Die Aktion `server_updater.run_maintenance` führt eine komplette Wartung
durch, gedacht für eine zeitgesteuerte Automation („sonntags 3 Uhr“). Pro
Server, nacheinander:

1. aktuellen Stand abfragen und Paket-Updates einspielen (falls vorhanden),
2. optional Compose-Container mit verfügbarem Image-Update aktualisieren
   (`update_containers`),
3. optional ungenutzte Docker-Images entfernen (`prune_images`),
4. erneut abfragen und abhängig von `reboot` neu starten: `never`,
   `if_required` (Standard: nur bei Neustart-Bedarf) oder `always`,
5. falls kein Neustart folgt: optional veraltete Dienste neu starten
   (`restart_services`, siehe needrestart).

Ohne `servers` werden alle Server gewartet. Ein Fehler auf einem Server hält
die anderen nicht auf; am Ende wirft die Aktion einen Fehler mit allen
fehlgeschlagenen Servern – oder liefert, mit `response_variable`, eine
Zusammenfassung pro Server:

```yaml
triggers:
  - trigger: time
    at: "03:00:00"
conditions:
  - condition: time
    weekday: sun
actions:
  - action: server_updater.run_maintenance
    data:
      reboot: if_required
      update_containers: true
      restart_services: true
      prune_images: true
    response_variable: wartung
  - action: notify.mobile_app_mein_handy
    data:
      message: >
        {% for s in wartung.servers %}{{ s.server }}: {{ s.packages_updated }} Pakete,
        {{ s.containers_updated | count }} Container,
        {{ 'neu gestartet' if s.rebooted else 'kein Neustart' }}
        {{ ('FEHLER: ' ~ s.error) if s.error else '' }}
        {% endfor %}
```

Felder der Zusammenfassung: `server`, `packages_updated`,
`containers_updated`, `images_pruned`, `services_restarted`,
`reboot_required`, `rebooted`, `error`.

> Bis Version 0.3.x gab es stattdessen `binary_sensor.<server>_updates_available`.
> Er wurde in 0.4.0 entfernt; der Registry-Eintrag wird beim Start
> automatisch aufgeräumt.

## Docker-Container-Updates

Läuft auf dem Server Docker, inventarisiert die Integration zusätzlich die
laufenden Container und prüft, ob für deren Image eine neuere Version
vorliegt – z. B. für einen Caddy-Container oder selbst gebaute Images aus
GitHub-Actions-Workflows (GHCR).

**Wie die Erkennung funktioniert:**

1. Beim ersten Laden der Integration (und über den Button
   `Container neu inventarisieren`) wird per SSH `docker ps -a`/`docker inspect`
   ausgeführt, um Container und deren Image-Referenz zu ermitteln.
   Erfasst werden laufende Container sowie gestoppte, die laufen sollen
   (Neustart-Richtlinie `always`, `unless-stopped` oder `on-failure`) oder
   schon bekannt waren – ein aus Home Assistant gestoppter Container bleibt
   so steuerbar, alte beendete Einweg-Container bekommen aber kein Gerät.
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
   Registries; oder lokal gebaute Images ohne bekannte Registry-Herkunft
   (z. B. per `docker build` / Compose-`build:` erzeugt – hier fehlt der
   `RepoDigest`, mit dem sich der laufende Stand vergleichen ließe).
   Verschwindet ein Container beim nächsten Rescan, wird seine Entität
   samt Gerät automatisch entfernt.

Entitäten und Geräte sind an den **Containernamen** gebunden, nicht an die
Container-ID. Wird ein Container neu erstellt (Update über den
Installieren-Button, `docker compose up --force-recreate`, …), bekommt er
eine neue ID, behält aber Namen, Entität und Entitäts-ID. Verwaiste
Entitäten/Geräte älterer Versionen (≤ 0.3.0, die noch an die Container-ID
gebunden waren und dadurch Duplikate wie `update.<name>_2` erzeugt haben)
werden beim ersten erfolgreichen Scan nach dem Update automatisch entfernt.

**Container steuern**: Jeder erfasste Container bekommt ein eigenes Gerät –
auch solche ohne `update`-Entität (z. B. lokal gebaute Images) – mit

- Button `Neu starten` (`docker restart <name>`; der Container behält ID und
  Konfiguration, ein Neu-Inventarisieren ist nicht nötig) und
- Schalter `Läuft` (`docker start`/`docker stop`). Attribute `state`
  (z. B. `running`, `exited`) und `status` (z. B. `Up 3 hours`).

Der Laufzustand wird bei jeder regulären Abfrage mit einem einzigen
`docker ps -a` per SSH gelesen (und sofort nach jeder eigenen Aktion). Ist der
Server dabei nicht erreichbar, wird der Schalter `unavailable`, die
`update`-Entitäten (Registry-Abfrage) bleiben davon unberührt.

**Images aufräumen**: Der Server-Button `Ungenutzte Images aufräumen`
führt `docker image prune -f` aus und entfernt damit Images ohne Tag, wie
sie nach Updates zurückbleiben. Getaggte, gerade nicht genutzte Images bleiben
erhalten. Der freigegebene Speicher steht im Log und im Ereignis
(`reclaimed`).

Jede `update`-Entität zeigt die (verkürzten) Digests als installierte/neueste
Version und – sofern das Image das OCI-Label
`org.opencontainers.image.source` trägt (bei GitHub-Actions-Builds meist
automatisch gesetzt) – einen Link zum Quell-Repository.

**"Installieren"-Button – nur für Docker-Compose-verwaltete Container**: Setzt
Docker Compose (v2, `docker compose ...`) den Container auf, hinterlegt es
automatisch die Container-Labels `com.docker.compose.service` und
`com.docker.compose.project.config_files` (Pfad zur `docker-compose.yml`).
Sie werden aus `docker inspect <container>` gelesen (nicht aus den
Image-Labels); das Attribut `compose_service` der Entität zeigt den
erkannten Service an.
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

### IP-Adresse oder Zugangsdaten ändern

Ändern sich Host/IP, Port, Benutzer, Passwort, Schlüssel oder die
sudo-Einstellungen, muss der Server nicht gelöscht werden: Einstellungen →
Geräte & Dienste → Server Updater → beim Eintrag im Menü (⋮)
**„Neu konfigurieren“**. Felder für Passwort, Schlüssel, Passphrase und
Sudo-Passwort sind dort leer – **leer lassen behält den gespeicherten Wert**.
Die Verbindung wird vor dem Speichern getestet; Entitäten, Verlauf und
Automationen bleiben erhalten.

Wird die Anmeldung später abgelehnt (z. B. Passwort auf dem Server
geändert), startet Home Assistant automatisch eine **erneute
Authentifizierung** (Hinweis unter Geräte & Dienste), in der sich die
Anmeldedaten direkt korrigieren lassen.

### SSH-Hostschlüssel

Beim ersten Verbinden merkt sich die Integration den SSH-Hostschlüssel des
Servers (Trust on first use, bestehende Einträge beim ersten Abruf nach dem
Update auf 0.7.0). Danach wird jede Verbindung abgelehnt, deren Hostschlüssel
abweicht – **bevor** Passwörter übertragen werden. Das schützt vor
Man-in-the-Middle-Angriffen und davor, Zugangsdaten an einen falschen Rechner
zu schicken, wenn eine IP neu vergeben wurde.

Hat sich der Schlüssel legitim geändert (Neuinstallation, neue IP zeigt auf
einen anderen Server), zeigt die erneute Authentifizierung bzw. „Neu
konfigurieren“ den **Fingerabdruck** des neuen Schlüssels an. Diesen auf dem
Server vergleichen, z. B.:

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

und nur bei Übereinstimmung „Neuen Hostschlüssel vertrauen“ ankreuzen.

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

Je nach genutzten Funktionen zusätzlich:

| Funktion | Befehl |
|---|---|
| Docker (Inventar, Installieren, Neustart/Start/Stop, Images aufräumen) | `/usr/bin/docker` |
| Dienste mit veralteten Bibliotheken | `/usr/sbin/needrestart`, `/usr/bin/systemctl restart *` |
| Proxmox VE: Autostart-Gäste prüfen | `/usr/bin/pvesh` |

`docker` lässt sich alternativ über die Gruppe `docker` freigeben. Fehlt ein
Recht, bleibt die jeweilige Funktion leer bzw. meldet einen Fehler; der Rest
funktioniert weiter.

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
  datenbank gespeichert. Beim Neu-Konfigurieren werden gespeicherte
  Passwörter/Schlüssel nicht an die Oberfläche zurückgegeben.
- Der SSH-Hostschlüssel wird beim ersten Verbinden gespeichert und danach
  geprüft (siehe [SSH-Hostschlüssel](#ssh-hostschlüssel)). Es werden nur die
  in der Integration hinterlegten Zugangsdaten verwendet – keine Schlüssel,
  kein SSH-Agent und keine `~/.ssh/config` des Home-Assistant-Hosts.
- Werte vom Zielserver (Container-, Image-, Compose- und Dienstnamen) werden
  vor der Verwendung in Befehlen shell-sicher maskiert.

## Technischer Hintergrund

Die Integration verbindet sich bei jeder Prüfung bzw. jedem Update kurzzeitig
per SSH ([asyncssh](https://asyncssh.readthedocs.io/)) mit dem Zielserver,
es wird keine dauerhafte Verbindung gehalten. Da Debian, Ubuntu und
Proxmox VE alle auf `apt` basieren, kommt derselbe Befehlssatz
(`apt list --upgradable`, `apt-get dist-upgrade`, `/var/run/reboot-required`)
für alle drei Systeme zum Einsatz.

## Troubleshooting

**Sensor „Neu zu startende Dienste“ / Button „Dienste neu starten“ fehlt**
- Erscheint nur, wenn `/usr/sbin/needrestart` beim Laden der Integration
  vorhanden war: `apt install needrestart`, dann Integration neu laden

**„Autostart-Gäste gestoppt“ ist `unknown`**
- `pvesh` konnte nicht gelesen werden – meist fehlt das Sudo-Recht für
  `/usr/bin/pvesh` (siehe Sudo-Konfiguration)

**Neustart wird als fehlgeschlagen gemeldet, obwohl der Server läuft**
- Grund steht in der Benachrichtigung bzw. im Ereignis: „nicht erreichbar“
  (Server braucht länger als 15 Minuten), „Startzeitpunkt unverändert“ (der
  `reboot`-Befehl wurde nicht ausgeführt, z. B. fehlendes Sudo-Recht oder
  ein systemd-Inhibitor) oder eine Liste nicht gestarteter Gäste

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
  Minuten dauern); Fortschritt und Ergebnis sind an der Entität
  `Systempakete` sichtbar (alternativ dort direkt „Installieren“ nutzen). Details stehen im Home-Assistant-Log
  (Einstellungen → System → Protokolle, Filter `server_updater`)
- Während ein Update läuft, ist die Entität `unavailable`, um Doppel-Presses
  zu verhindern

**Entitäten bleiben nach einem Neustart `unavailable`**
- Die Integration fragt den Server ca. drei Minuten nach dem Neustart-Befehl
  und danach minütlich bis zu 15 Minuten lang erneut ab. Braucht der Server
  noch länger, aktualisieren sich die Sensoren beim nächsten regulären
  Poll-Intervall oder nach manuellem "Neu laden" der Integration

**Reparatur-Hinweis „Neustart erforderlich“ verschwindet auf Proxmox VE nicht**
- Der Hinweis vergleicht den laufenden Kernel mit dem neuesten unter
  `/boot`. Ist per `proxmox-boot-tool kernel pin` bewusst ein älterer
  Kernel fixiert, bleibt der Hinweis bestehen – dann die nicht benötigten
  neueren Kernel entfernen oder den Pin aufheben
- Attribute `running_kernel`/`newest_kernel` von
  `binary_sensor.<server>_reboot_required` zeigen den Vergleich

**Keine `update`-Entitäten für Docker-Container, obwohl welche laufen**
- Erwartet, wenn die Registry nicht anonym abfragbar ist (privates Image),
  das Image auf einen Digest statt einen Tag fixiert ist, oder es lokal
  ohne Registry-Herkunft gebaut wurde – siehe
  [Docker-Container-Updates](#docker-container-updates)
- `docker ps`/`docker inspect` auf dem Server manuell mit dem konfigurierten
  SSH-Nutzer (bzw. `sudo docker ...`) testen
- Nach dem Deployen neuer Container den Button
  `Container neu inventarisieren` drücken

**Doppelte Container-Entitäten (`update.<name>_2`, `_3`, …)**
- Entstanden in Version ≤ 0.3.0 bei jedem Neuerstellen eines Containers.
  Ab 0.3.1 werden sie beim ersten erfolgreichen Scan entfernt; danach
  ggf. die neu angelegte Entität in `update.<name>` umbenennen, falls
  sie noch ein Suffix trägt (Automationen/Dashboards prüfen)

**`update`-Entität hat keinen Installieren-Button**
- Attribut `compose_service` prüfen: ist es leer, wurde der Container nicht
  als Compose-Service erkannt
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
