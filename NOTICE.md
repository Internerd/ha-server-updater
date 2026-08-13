# Hinweise (Legal Notices)

## KI-Unterstützung bei der Erstellung

Große Teile dieses Projekts (Code, Konfiguration, Dokumentation) wurden mit
Unterstützung eines KI-Assistenten (Claude Code / Claude, Anthropic) erstellt.
Das ist auch in der Commit-Historie über den `Co-Authored-By: Claude ...`-
Trailer nachvollziehbar.

Da diese Integration SSH-Zugangsdaten entgegennimmt und mit Root-Rechten
Befehle (`apt-get`, `reboot`) auf deinen Servern ausführt, wird empfohlen,
den Code – insbesondere `ssh_client.py` und die Sudo-Befehlskonstruktion in
`ServerConnection._run` – vor produktivem Einsatz selbst zu lesen und zu
prüfen, statt der Beschreibung blind zu vertrauen.

## Kein Zusammenhang zu Drittmarken

Dieses Projekt ist ein unabhängiges, nicht offizielles Community-Projekt.
Es besteht keine Verbindung zu und keine Unterstützung durch:

- die Home Assistant Foundation / Open Home Foundation ("Home Assistant"
  ist eine Marke der jeweiligen Rechteinhaber)
- Canonical Ltd. ("Ubuntu")
- die Debian-Projektgemeinschaft ("Debian")
- Proxmox Server Solutions GmbH ("Proxmox VE")

Alle genannten Namen dienen ausschließlich der technischen Beschreibung der
unterstützten Zielsysteme.

## Lizenz dieses Projekts

Der in diesem Repository enthaltene, selbst geschriebene Code steht unter
der [MIT-Lizenz](LICENSE) ("AS IS", ohne Gewährleistung, siehe dort für den
vollständigen Haftungsausschluss).

## Lizenzen von Abhängigkeiten

Diese Integration wird nicht mit Home Assistant oder Drittbibliotheken
ausgeliefert (kein Vendoring) – beide werden von Home Assistant zur
Laufzeit über `manifest.json`/`requirements.txt` als separate Pakete
installiert. Für diese gilt jeweils die eigene Lizenz der Autor:innen:

| Abhängigkeit | Lizenz | Hinweis |
|---|---|---|
| [Home Assistant Core](https://github.com/home-assistant/core) | Apache License 2.0 | Wird nur als Laufzeitumgebung/API genutzt, nicht modifiziert oder mitverteilt |
| [asyncssh](https://github.com/ronf/asyncssh) | EPL-2.0 *oder* GPL-2.0-or-later (nach Wahl) | In `manifest.json` als `requirements` deklariert, wird von pip separat installiert |

## Haftungsausschluss

Diese Software führt auf Wunsch destruktive/systemverändernde Aktionen
(Paket-Updates, Neustarts) auf von dir konfigurierten Servern aus. Teste sie
zunächst auf unkritischen Systemen und stelle sicher, dass aktuelle Backups
vorhanden sind, bevor du sie gegen produktive Server einsetzt. Es besteht,
wie in der [MIT-Lizenz](LICENSE) festgehalten, keinerlei Gewährleistung oder
Haftung der Autor:innen.
