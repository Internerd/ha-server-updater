"""SSH client wrapper handling update checks and updates on Linux servers.

Targets Debian, Ubuntu and Proxmox VE, which are all apt-based, so a single
set of commands works across all three.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field

import asyncssh

from .const import (
    APT_FETCH_RETRY_DELAY,
    COMMAND_TIMEOUT,
    CONNECT_TIMEOUT,
    REBOOT_TIMEOUT,
    UPDATE_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


class ServerUpdaterError(Exception):
    """Base error for this integration."""


class ServerConnectionError(ServerUpdaterError):
    """Raised when the SSH connection cannot be established."""


class ServerCommandError(ServerUpdaterError):
    """Raised when a remote command fails."""


@dataclass
class UpgradablePackage:
    """One package with a pending upgrade, as reported by apt."""

    name: str
    new_version: str | None = None
    current_version: str | None = None


@dataclass
class UpdateCheckResult:
    """Result of a check for available updates and reboot state."""

    update_count: int
    packages: list[UpgradablePackage] = field(default_factory=list)
    reboot_required: bool = False
    reboot_required_packages: list[str] = field(default_factory=list)
    os_name: str | None = None
    os_version: str | None = None


@dataclass
class DockerContainerInfo:
    """A running Docker container and its image's known provenance.

    `labels` holds the image's labels merged with the container's own
    labels (the latter win on conflicts).
    """

    id: str
    name: str
    image_ref: str
    image_digest: str | None
    labels: dict[str, str]


class ServerConnection:
    """Wraps a short-lived SSH connection to a single server."""

    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str | None = None,
        private_key: str | None = None,
        private_key_passphrase: str | None = None,
        use_sudo: bool = True,
        sudo_password: str | None = None,
        refresh_apt_cache: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._private_key = private_key
        self._private_key_passphrase = private_key_passphrase
        self._use_sudo = use_sudo
        self._sudo_password = sudo_password
        self._refresh_apt_cache = refresh_apt_cache
        self._conn: asyncssh.SSHClientConnection | None = None

    async def __aenter__(self) -> "ServerConnection":
        await self._connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def _connect(self) -> None:
        client_keys = None
        if self._private_key:
            # Normalize copy-pasted key content: web textareas commonly
            # introduce CRLF line endings or per-line trailing whitespace,
            # both of which make PEM/OpenSSH key parsers fail with an
            # unhelpful "Invalid private key" error.
            normalized_key = "\n".join(
                line.rstrip() for line in self._private_key.splitlines()
            ).strip()
            try:
                client_keys = [
                    asyncssh.import_private_key(
                        normalized_key, passphrase=self._private_key_passphrase or None
                    )
                ]
            except asyncssh.KeyImportError as err:
                raise ServerConnectionError(
                    f"Ungültiger privater Schlüssel ({err}). Bitte den vollständigen "
                    "Inhalt der privaten Schlüsseldatei einfügen, inklusive der "
                    "-----BEGIN...-----/-----END...----- Zeilen."
                ) from err

        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    self._host,
                    port=int(self._port),
                    username=self._username,
                    password=self._password or None,
                    client_keys=client_keys,
                    known_hosts=None,
                ),
                timeout=CONNECT_TIMEOUT,
            )
        except asyncio.TimeoutError as err:
            raise ServerConnectionError(
                f"Zeitüberschreitung beim Verbinden mit {self._host}:{self._port}"
            ) from err
        except (asyncssh.Error, OSError) as err:
            raise ServerConnectionError(f"Verbindung zu {self._host} fehlgeschlagen: {err}") from err

    async def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            try:
                await self._conn.wait_closed()
            except (asyncssh.Error, OSError):
                pass
            self._conn = None

    async def _run(
        self, command: str, use_sudo: bool = False, timeout: int = COMMAND_TIMEOUT
    ) -> asyncssh.SSHCompletedProcess:
        if self._conn is None:
            raise ServerConnectionError("Nicht verbunden")

        stdin_data = None
        if use_sudo and self._use_sudo:
            if self._sudo_password:
                full_command = f"sudo -S -p '' -- {command}"
                stdin_data = f"{self._sudo_password}\n"
            else:
                full_command = f"sudo -n -- {command}"
        else:
            full_command = command

        try:
            return await asyncio.wait_for(
                self._conn.run(full_command, input=stdin_data, check=False),
                timeout=timeout,
            )
        except asyncio.TimeoutError as err:
            raise ServerCommandError(
                f"Zeitüberschreitung beim Ausführen von '{command}'"
            ) from err
        except asyncssh.Error as err:
            raise ServerCommandError(f"Befehl '{command}' fehlgeschlagen: {err}") from err

    async def async_test_connection(self) -> str:
        """Connect and return the remote OS pretty name, validating sudo if configured."""
        os_name = await self._get_os_name()

        if self._use_sudo:
            result = await self._run("true", use_sudo=True)
            if result.exit_status != 0:
                raise ServerConnectionError(
                    "Sudo-Prüfung fehlgeschlagen. Bitte sudo-Passwort oder "
                    "NOPASSWD-Konfiguration prüfen."
                )

        return os_name

    async def _get_os_release(self) -> dict[str, str]:
        result = await self._run("cat /etc/os-release")
        if result.exit_status != 0:
            return {}
        return _parse_os_release(str(result.stdout or ""))

    async def _get_os_name(self) -> str | None:
        return (await self._get_os_release()).get("PRETTY_NAME")

    async def _get_os_version(self, os_release: dict[str, str]) -> str | None:
        """Return a human-readable, point-release-precise OS version.

        Proxmox VE reports its own version via pveversion; Debian's point
        release is only in /etc/debian_version; Ubuntu has it in VERSION.
        """
        pve = await self._run("pveversion 2>/dev/null || true")
        match = re.match(r"pve-manager/([^/\s]+)", str(pve.stdout or "").strip())
        if match:
            return f"Proxmox VE {match.group(1)}"

        if os_release.get("ID") == "debian":
            debian = await self._run("cat /etc/debian_version 2>/dev/null || true")
            if version := str(debian.stdout or "").strip():
                return version

        if version := os_release.get("VERSION"):
            return version.split(" (", 1)[0]
        return os_release.get("VERSION_ID")

    async def async_check_updates(self) -> UpdateCheckResult:
        """Check for available package updates and whether a reboot is required."""
        os_release = await self._get_os_release()
        os_version = await self._get_os_version(os_release)

        if self._refresh_apt_cache:
            refresh = await self._run(
                "apt-get update -qq", use_sudo=True, timeout=COMMAND_TIMEOUT
            )
            if refresh.exit_status != 0:
                _LOGGER.warning(
                    "apt-get update auf %s fehlgeschlagen (Exit %s): %s",
                    self._host,
                    refresh.exit_status,
                    str(refresh.stderr).strip(),
                )

        list_result = await self._run("apt list --upgradable")
        packages = _parse_upgradable(str(list_result.stdout or ""))

        reboot_result = await self._run(
            "test -f /var/run/reboot-required && echo yes || echo no"
        )
        reboot_required = str(reboot_result.stdout or "").strip() == "yes"

        reboot_packages: list[str] = []
        if reboot_required:
            pkgs_result = await self._run(
                "cat /var/run/reboot-required.pkgs 2>/dev/null || true"
            )
            reboot_packages = [
                line.strip()
                for line in str(pkgs_result.stdout or "").splitlines()
                if line.strip()
            ]

        return UpdateCheckResult(
            update_count=len(packages),
            packages=packages,
            reboot_required=reboot_required,
            reboot_required_packages=reboot_packages,
            os_name=os_release.get("PRETTY_NAME"),
            os_version=os_version,
        )

    async def async_apply_updates(self) -> None:
        """Run apt-get update and a non-interactive dist-upgrade.

        If downloading packages fails (typically a 404 because the mirror's
        package index references files it doesn't serve yet), the package
        lists are refreshed once more, bypassing HTTP caches, and the upgrade
        is retried once - which is what apt itself suggests in that case.
        apt doesn't install anything when a download fails, so the retry is
        safe.
        """
        await self._async_apt_update("apt-get update -qq")

        upgrade = await self._async_dist_upgrade()
        if upgrade.exit_status != 0 and _is_fetch_failure(str(upgrade.stderr)):
            _LOGGER.warning(
                "Pakete auf %s konnten nicht heruntergeladen werden, aktualisiere "
                "Paketlisten und versuche es in %s s erneut: %s",
                self._host,
                APT_FETCH_RETRY_DELAY,
                str(upgrade.stderr).strip(),
            )
            await asyncio.sleep(APT_FETCH_RETRY_DELAY)
            await self._async_apt_update(
                "apt-get update -qq -o Acquire::http::No-Cache=true"
            )
            upgrade = await self._async_dist_upgrade()

        if upgrade.exit_status != 0:
            stderr = str(upgrade.stderr).strip()
            if _is_fetch_failure(stderr):
                raise ServerCommandError(
                    "Paketquelle liefert einzelne Pakete nicht aus (vermutlich wird "
                    "der Mirror gerade synchronisiert), auch nach erneutem "
                    "apt-get update. Es wurde nichts installiert, bitte später "
                    f"erneut versuchen. Details: {stderr}"
                )
            raise ServerCommandError(f"apt-get dist-upgrade fehlgeschlagen: {stderr}")

    async def _async_apt_update(self, command: str) -> None:
        update = await self._run(command, use_sudo=True, timeout=COMMAND_TIMEOUT)
        if update.exit_status != 0:
            raise ServerCommandError(
                f"apt-get update fehlgeschlagen: {str(update.stderr).strip()}"
            )

    async def _async_dist_upgrade(self) -> asyncssh.SSHCompletedProcess:
        return await self._run(
            "DEBIAN_FRONTEND=noninteractive apt-get -y "
            '-o Dpkg::Options::="--force-confold" dist-upgrade',
            use_sudo=True,
            timeout=UPDATE_TIMEOUT,
        )

    async def async_list_docker_containers(self) -> list[DockerContainerInfo]:
        """List running Docker containers and each one's image provenance.

        Returns an empty list if Docker isn't installed or reachable rather
        than raising, since not every server configured in this integration
        is expected to run Docker.
        """
        ps_result = await self._run("docker ps --format '{{.ID}}'", use_sudo=True)
        if ps_result.exit_status != 0:
            _LOGGER.debug(
                "docker ps auf %s nicht verfügbar (Exit %s): %s",
                self._host,
                ps_result.exit_status,
                str(ps_result.stderr).strip(),
            )
            return []

        container_ids = [
            line.strip() for line in str(ps_result.stdout or "").splitlines() if line.strip()
        ]
        if not container_ids:
            return []

        inspect_result = await self._run(
            f"docker inspect {' '.join(container_ids)}", use_sudo=True
        )
        try:
            containers_raw = json.loads(str(inspect_result.stdout or "[]"))
        except json.JSONDecodeError as err:
            raise ServerCommandError(
                f"Antwort von 'docker inspect' nicht lesbar: {err}"
            ) from err

        containers: list[DockerContainerInfo] = []
        image_cache: dict[str, dict] = {}
        for entry in containers_raw:
            image_ref = (entry.get("Config") or {}).get("Image", "")
            if image_ref and image_ref not in image_cache:
                image_cache[image_ref] = await self._async_inspect_docker_image(image_ref)
            info = image_cache.get(image_ref, {})
            # Docker Compose sets its com.docker.compose.* labels on the
            # container, not on the image, while OCI labels such as
            # org.opencontainers.image.source live on the image. Merge both,
            # with container labels taking precedence.
            labels = {
                **info.get("labels", {}),
                **((entry.get("Config") or {}).get("Labels") or {}),
            }
            containers.append(
                DockerContainerInfo(
                    id=(entry.get("Id") or "")[:12],
                    name=(entry.get("Name") or "").lstrip("/"),
                    image_ref=image_ref,
                    image_digest=info.get("digest"),
                    labels=labels,
                )
            )
        return containers

    async def _async_inspect_docker_image(self, image_ref: str) -> dict:
        result = await self._run(f"docker image inspect {image_ref}", use_sudo=True)
        if result.exit_status != 0:
            return {}
        try:
            images_raw = json.loads(str(result.stdout or "[]"))
        except json.JSONDecodeError:
            return {}
        if not images_raw:
            return {}

        image = images_raw[0]
        repo_digests = image.get("RepoDigests") or []
        digest = (
            repo_digests[0].split("@", 1)[1]
            if repo_digests and "@" in repo_digests[0]
            else None
        )
        return {"digest": digest, "labels": (image.get("Config") or {}).get("Labels") or {}}

    async def async_check_paths_exist(self, paths: list[str]) -> bool:
        """Check whether all given absolute paths exist on the server."""
        if not paths:
            return False
        check = " && ".join(f"test -f '{path}'" for path in paths)
        result = await self._run(check, use_sudo=True)
        return result.exit_status == 0

    async def async_apply_compose_update(
        self, config_files: list[str], service: str, working_dir: str | None = None
    ) -> None:
        """Pull and recreate a single Docker Compose service.

        Uses `docker compose` (v2) against the container's own recorded
        compose files/service, so the original volumes/networks/env are
        preserved instead of being reconstructed by hand.
        """
        file_args = " ".join(f"-f '{path}'" for path in config_files)
        project_dir_arg = f"--project-directory '{working_dir}' " if working_dir else ""

        pull = await self._run(
            f"docker compose {project_dir_arg}{file_args} pull {service}",
            use_sudo=True,
            timeout=UPDATE_TIMEOUT,
        )
        if pull.exit_status != 0:
            raise ServerCommandError(
                f"docker compose pull fehlgeschlagen: {str(pull.stderr).strip()}"
            )

        up = await self._run(
            f"docker compose {project_dir_arg}{file_args} up -d {service}",
            use_sudo=True,
            timeout=UPDATE_TIMEOUT,
        )
        if up.exit_status != 0:
            raise ServerCommandError(
                f"docker compose up fehlgeschlagen: {str(up.stderr).strip()}"
            )

    async def async_reboot(self) -> None:
        """Reboot the remote server. The connection is expected to drop."""
        try:
            await self._run("reboot", use_sudo=True, timeout=REBOOT_TIMEOUT)
        except (ServerCommandError, asyncssh.Error, ConnectionResetError, OSError):
            # The SSH session is torn down by the reboot itself, which
            # commonly surfaces as a connection error rather than a
            # clean command result. This is the expected outcome.
            _LOGGER.debug(
                "Verbindung zu %s nach Reboot-Befehl unterbrochen (erwartet)", self._host
            )


_UPGRADABLE_RE = re.compile(
    r"^(?P<name>[^/\s]+)/\S+\s+(?P<new>\S+)\s+\S+"
    r"(?:\s+\[upgradable from: (?P<current>[^\]]+)\])?"
)


def _parse_upgradable(output: str) -> list[UpgradablePackage]:
    """Parse `apt list --upgradable` output.

    Lines look like:
    openssl/stable-security 3.0.15-1~deb12u1 amd64 [upgradable from: 3.0.14-1~deb12u2]
    """
    packages: list[UpgradablePackage] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Listing...") or "/" not in line:
            continue
        match = _UPGRADABLE_RE.match(line)
        if match:
            packages.append(
                UpgradablePackage(
                    name=match.group("name"),
                    new_version=match.group("new"),
                    current_version=match.group("current"),
                )
            )
        else:
            packages.append(UpgradablePackage(name=line.split("/", 1)[0].strip()))
    return packages


def _is_fetch_failure(stderr: str) -> bool:
    """Return True if apt failed because package files couldn't be downloaded."""
    return "Unable to fetch some archives" in stderr or "Failed to fetch" in stderr


def _parse_os_release(output: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in output.splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"')
    return values
