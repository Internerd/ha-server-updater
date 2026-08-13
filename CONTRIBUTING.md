# Contributing

Danke für dein Interesse an diesem Projekt! Issues und Pull Requests sind willkommen.

## Entwicklung

1. Repository forken und clonen
2. `custom_components/server_updater` per Symlink oder Kopie in eine
   Home-Assistant-Testinstanz einbinden (z. B. per
   [devcontainer](https://developers.home-assistant.io/docs/development_environment)
   oder einer separaten HA-Instanz in einer VM/Proxmox-Container)
3. Änderungen vornehmen und gegen mindestens einen echten Debian-, Ubuntu-
   oder Proxmox-VE-Server testen

## Sanity-Checks vor einem PR

```bash
python3 -m py_compile custom_components/server_updater/*.py
python3 -m pyflakes custom_components/server_updater/*.py
```

Zusätzlich validieren zwei GitHub Actions (`.github/workflows/validate.yml`)
jeden Push/PR automatisch gegen [hassfest](https://developers.home-assistant.io/docs/creating_integration_manifest/#manifestjson)
und die [HACS-Anforderungen](https://hacs.xyz/docs/publish/integration/).

## Pull Requests

- Kleine, fokussierte Änderungen bevorzugt
- `CHANGELOG.md` unter `[Unreleased]` ergänzen
- Bei neuen Konfigurationsoptionen: `strings.json` **und** beide Dateien in
  `translations/` (`de.json`, `en.json`) aktualisieren

## Release-Prozess (für Maintainer)

1. Version in `custom_components/server_updater/manifest.json` erhöhen
   (SemVer)
2. `CHANGELOG.md` aktualisieren
3. Git-Tag `vX.Y.Z` erstellen und als GitHub Release veröffentlichen –
   HACS nutzt Releases, um Nutzern Updates anzuzeigen
