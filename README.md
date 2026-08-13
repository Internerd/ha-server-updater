# ha-server-updater

Home Assistant Custom Integration, die Linux-Server (Debian, Ubuntu, Proxmox VE)
per SSH auf verfügbare Paket-Updates prüft und Updates/Reboots direkt aus
Home Assistant heraus anstoßen kann.

Es lassen sich beliebig viele Server konfigurieren, jeder als eigener
"Config Entry" mit eigenem Gerät und eigenen Entitäten.

## Bereitgestellte Entitäten (pro Server)

| Entität | Typ | Beschreibung |
|---|---|---|
| `binary_sensor.<server>_updates_available` | binary_sensor | An, wenn Paket-Updates verfügbar sind. Attribute: `update_count`, `packages`, `os_name` |
| `binary_sensor.<server>_reboot_required` | binary_sensor | An, wenn der Server einen Neustart benötigt (`/var/run/reboot-required`). Attribut: `reboot_required_packages` |
| `button.<server>_update` | button | Führt `apt-get update && apt-get dist-upgrade` auf dem Server aus |
| `button.<server>_update_and_reboot` | button | Wie oben, anschließend Neustart des Servers |

Nach einem Tastendruck laufen Update/Reboot im Hintergrund; danach werden die
Sensoren automatisch aktualisiert.

## Installation

### Über HACS (empfohlen)

1. HACS → Integrationen → Menü (⋮) → "Benutzerdefinierte Repositories"
2. Dieses Repository als Typ "Integration" hinzufügen
3. "Server Updater" installieren und Home Assistant neu starten

### Manuell

`custom_components/server_updater` in das `custom_components`-Verzeichnis
der Home-Assistant-Konfiguration kopieren und Home Assistant neu starten.

## Einrichtung

Einstellungen → Geräte & Dienste → Integration hinzufügen → "Server Updater".
Der Assistent fragt ab:

1. **Name, Host/IP, SSH-Port, Benutzername**
2. **Authentifizierung**: Passwort oder privater SSH-Schlüssel (Inhalt der
   Datei, z. B. `~/.ssh/id_ed25519`, optional mit Passphrase)
3. **Sudo**: Ob Befehle mit `sudo` ausgeführt werden sollen (für Updates und
   Reboot i. d. R. notwendig), und optional ein Sudo-Passwort

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

## Hinweis zu Add-on vs. Integration

Diese Lösung ist als HA-Integration umgesetzt, da sie so am direktesten
Entitäten bereitstellt, mehrere Server über normale Config Entries
unterstützt und ohne zusätzlichen Container auskommt. Ein separates
Supervisor-Add-on wäre für denselben Funktionsumfang nur zusätzliche,
redundante Infrastruktur (eigener Container, eigene Anbindung der Entitäten
via MQTT/REST) – bei Bedarf kann das aber ergänzt werden.
