"""SSH client wrapper handling update checks and updates on Linux servers.

Targets Debian, Ubuntu and Proxmox VE, which are all apt-based, so a single
set of commands works across all three.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import asyncssh

from .const import (
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
class UpdateCheckResult:
    """Result of a check for available updates and reboot state."""

    update_count: int
    packages: list[str] = field(default_factory=list)
    reboot_required: bool = False
    reboot_required_packages: list[str] = field(default_factory=list)
    os_name: str | None = None


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
            try:
                client_keys = [
                    asyncssh.import_private_key(
                        self._private_key, passphrase=self._private_key_passphrase
                    )
                ]
            except asyncssh.KeyImportError as err:
                raise ServerConnectionError(f"Ungültiger privater Schlüssel: {err}") from err

        try:
            self._conn = await asyncio.wait_for(
                asyncssh.connect(
                    self._host,
                    port=self._port,
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

    async def _get_os_name(self) -> str | None:
        result = await self._run("cat /etc/os-release")
        if result.exit_status != 0:
            return None
        pretty_name = None
        for line in str(result.stdout).splitlines():
            if line.startswith("PRETTY_NAME="):
                pretty_name = line.split("=", 1)[1].strip().strip('"')
                break
        return pretty_name

    async def async_check_updates(self) -> UpdateCheckResult:
        """Check for available package updates and whether a reboot is required."""
        os_name = await self._get_os_name()

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
            os_name=os_name,
        )

    async def async_apply_updates(self) -> None:
        """Run apt-get update and a non-interactive dist-upgrade."""
        update = await self._run(
            "apt-get update -qq", use_sudo=True, timeout=COMMAND_TIMEOUT
        )
        if update.exit_status != 0:
            raise ServerCommandError(
                f"apt-get update fehlgeschlagen: {str(update.stderr).strip()}"
            )

        upgrade = await self._run(
            "DEBIAN_FRONTEND=noninteractive apt-get -y "
            '-o Dpkg::Options::="--force-confold" dist-upgrade',
            use_sudo=True,
            timeout=UPDATE_TIMEOUT,
        )
        if upgrade.exit_status != 0:
            raise ServerCommandError(
                f"apt-get dist-upgrade fehlgeschlagen: {str(upgrade.stderr).strip()}"
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


def _parse_upgradable(output: str) -> list[str]:
    packages: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if not line or line.startswith("Listing..."):
            continue
        packages.append(line.split("/", 1)[0].strip())
    return packages
