"""DataUpdateCoordinator for the Server Updater integration."""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import (
    CONF_HOST_KEY,
    CONF_PRIVATE_KEY,
    CONF_PRIVATE_KEY_PASSPHRASE,
    CONF_REFRESH_APT_CACHE,
    CONF_SUDO_PASSWORD,
    CONF_USE_SUDO,
    DEFAULT_REFRESH_APT_CACHE,
    DEFAULT_USE_SUDO,
    DOMAIN,
    MAX_REBOOT_PACKAGES_IN_ISSUE,
    REBOOT_REFRESH_DELAY,
    REBOOT_RETRY_INTERVAL,
    REBOOT_WATCH_TIMEOUT,
    SERVICE_RESTART_EXCLUDE_PATTERNS,
)
from .reporting import async_report
from .ssh_client import (
    ServerAuthError,
    ServerConnection,
    ServerHostKeyError,
    ServerUpdaterError,
    UpdateCheckResult,
)

_LOGGER = logging.getLogger(__name__)

_SERVICE_RESTART_EXCLUDE_RE = re.compile("|".join(SERVICE_RESTART_EXCLUDE_PATTERNS))


def split_restartable_services(services: list[str]) -> tuple[list[str], list[str]]:
    """Split needrestart's services into (safe to restart, excluded)."""
    safe = [s for s in services if not _SERVICE_RESTART_EXCLUDE_RE.fullmatch(s)]
    excluded = [s for s in services if _SERVICE_RESTART_EXCLUDE_RE.fullmatch(s)]
    return safe, excluded


class ServerUpdaterCoordinator(DataUpdateCoordinator[UpdateCheckResult]):
    """Coordinates polling a single server for update / reboot state."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, update_interval: timedelta
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=update_interval,
        )
        self.entry = entry
        self.busy = False
        # True from a reboot command until the server is confirmed back (or
        # the reboot watch gave up).
        self.rebooting = False
        self.reboot_issue_placeholders: dict[str, str] = {}
        self._cancel_delayed_refresh: Callable[[], None] | None = None
        self._boot_time_before_reboot: int | None = None
        self._reboot_deadline: datetime | None = None
        entry.async_on_unload(self._async_cancel_delayed_refresh)

    def build_connection(self) -> ServerConnection:
        data = self.entry.data
        return ServerConnection(
            host=data[CONF_HOST],
            port=data.get(CONF_PORT, 22),
            username=data[CONF_USERNAME],
            password=data.get(CONF_PASSWORD),
            private_key=data.get(CONF_PRIVATE_KEY),
            private_key_passphrase=data.get(CONF_PRIVATE_KEY_PASSPHRASE),
            use_sudo=data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO),
            sudo_password=data.get(CONF_SUDO_PASSWORD),
            refresh_apt_cache=self.entry.options.get(
                CONF_REFRESH_APT_CACHE, DEFAULT_REFRESH_APT_CACHE
            ),
            host_key=data.get(CONF_HOST_KEY),
            on_host_key_learned=self._async_pin_host_key,
        )

    def _async_pin_host_key(self, host_key: str) -> None:
        """Pin the host key seen on first contact (entries from before 0.7.0)."""
        if self.entry.data.get(CONF_HOST_KEY):
            return
        _LOGGER.info("Hostschlüssel von %s wird ab jetzt geprüft", self.entry.title)
        self.hass.config_entries.async_update_entry(
            self.entry, data={**self.entry.data, CONF_HOST_KEY: host_key}
        )

    async def _async_update_data(self) -> UpdateCheckResult:
        try:
            async with self.build_connection() as conn:
                result = await conn.async_check_updates()
        except (ServerAuthError, ServerHostKeyError) as err:
            # Starts the reauthentication flow (Settings > Devices & services).
            raise ConfigEntryAuthFailed(str(err)) from err
        except ServerUpdaterError as err:
            raise UpdateFailed(str(err)) from err
        self._async_update_reboot_issue(result)
        return result

    @property
    def reboot_issue_id(self) -> str:
        return f"reboot_required_{self.entry.entry_id}"

    def _async_update_reboot_issue(self, result: UpdateCheckResult) -> None:
        """Show a pending reboot under Settings > Repairs, alongside other system notices.

        The issue is fixable: its repair flow reboots the server (see repairs.py).
        """
        if not result.any_reboot_required:
            ir.async_delete_issue(self.hass, DOMAIN, self.reboot_issue_id)
            return

        reasons = []
        if result.kernel_reboot_required:
            reasons.append(
                f"Neuer Kernel {result.newest_kernel} installiert, "
                f"aktiv ist noch {result.running_kernel}"
            )
        packages = result.reboot_required_packages
        if packages:
            package_text = ", ".join(packages[:MAX_REBOOT_PACKAGES_IN_ISSUE])
            if len(packages) > MAX_REBOOT_PACKAGES_IN_ISSUE:
                package_text += f" (+{len(packages) - MAX_REBOOT_PACKAGES_IN_ISSUE})"
            reasons.append(f"Pakete: {package_text}")

        self.reboot_issue_placeholders = {
            "name": self.entry.title,
            "reason": "; ".join(reasons) or "-",
        }
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.reboot_issue_id,
            data={"entry_id": self.entry.entry_id},
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="reboot_required",
            translation_placeholders=self.reboot_issue_placeholders,
        )

    @asynccontextmanager
    async def _async_busy(self) -> AsyncIterator[None]:
        """Mark the server busy for one operation; refuse overlapping ones."""
        if self.busy:
            raise ServerUpdaterError(
                f"Auf {self.entry.title} läuft bereits ein Vorgang, bitte später erneut versuchen"
            )
        self.busy = True
        self.async_update_listeners()
        try:
            yield
        finally:
            self.busy = False
            self.async_update_listeners()

    async def async_apply_updates(self, reboot: bool) -> None:
        """Apply updates and optionally reboot, then refresh state."""
        package_count = self.data.update_count if self.data else None
        async with self._async_busy():
            try:
                async with self.build_connection() as conn:
                    await conn.async_apply_updates()
                    if reboot:
                        await conn.async_reboot()
            except ServerUpdaterError as err:
                _LOGGER.error("Update für %s fehlgeschlagen: %s", self.entry.title, err)
                async_report(self.hass, self.entry, "update", str(err))
                raise

        async_report(self.hass, self.entry, "update", packages=package_count)
        if reboot:
            self._async_start_reboot_watch()
        else:
            await self.async_request_refresh()

    async def async_reboot(self) -> None:
        """Reboot the server without installing updates first."""
        async with self._async_busy():
            try:
                async with self.build_connection() as conn:
                    await conn.async_reboot()
            except ServerUpdaterError as err:
                _LOGGER.error("Neustart von %s fehlgeschlagen: %s", self.entry.title, err)
                async_report(self.hass, self.entry, "reboot", str(err))
                raise

        _LOGGER.info("Neustart von %s ausgelöst", self.entry.title)
        self._async_start_reboot_watch()

    async def async_restart_services(self) -> list[str]:
        """Restart the services needrestart reports, except excluded ones.

        Returns the restarted services.
        """
        services = self.data.services_to_restart if self.data else None
        if not services:
            raise ServerUpdaterError(
                f"Auf {self.entry.title} müssen keine Dienste neu gestartet werden"
            )
        safe, excluded = split_restartable_services(services)
        if not safe:
            raise ServerUpdaterError(
                "Nur Dienste ausstehend, die nicht automatisch neu gestartet werden "
                f"(Neustart des Servers nötig): {', '.join(excluded)}"
            )

        async with self._async_busy():
            try:
                async with self.build_connection() as conn:
                    await conn.async_restart_services(safe)
            except ServerUpdaterError as err:
                _LOGGER.error(
                    "Neustart der Dienste auf %s fehlgeschlagen: %s", self.entry.title, err
                )
                async_report(self.hass, self.entry, "services_restart", str(err))
                raise

        async_report(
            self.hass, self.entry, "services_restart", services=safe, excluded=excluded
        )
        await self.async_refresh()
        return safe

    def _async_start_reboot_watch(self) -> None:
        """Watch for the server to come back after a reboot command.

        Re-checks after REBOOT_REFRESH_DELAY (refreshing right away would hit
        the shutting-down server), then every REBOOT_RETRY_INTERVAL until the
        server is reachable, has a new boot time and - on Proxmox VE - all
        autostart guests run again, or REBOOT_WATCH_TIMEOUT has passed.
        """
        self._boot_time_before_reboot = self.data.boot_time if self.data else None
        self._reboot_deadline = dt_util.utcnow() + timedelta(seconds=REBOOT_WATCH_TIMEOUT)
        self.rebooting = True
        self.async_update_listeners()
        self._async_schedule_reboot_check(REBOOT_REFRESH_DELAY)

    def _async_schedule_reboot_check(self, delay: float) -> None:
        self._async_cancel_delayed_refresh()

        async def _async_check(_now: datetime) -> None:
            self._cancel_delayed_refresh = None
            await self._async_check_reboot()

        self._cancel_delayed_refresh = async_call_later(self.hass, delay, _async_check)

    async def _async_check_reboot(self) -> None:
        await self.async_refresh()
        problem = self.reboot_problem()
        if problem is not None and self._reboot_deadline and dt_util.utcnow() < self._reboot_deadline:
            self._async_schedule_reboot_check(REBOOT_RETRY_INTERVAL)
            return

        self.rebooting = False
        self._reboot_deadline = None
        self.async_update_listeners()
        if problem is None:
            _LOGGER.info("%s ist nach dem Neustart wieder bereit", self.entry.title)
        else:
            _LOGGER.error("Neustart von %s: %s", self.entry.title, problem)
        async_report(self.hass, self.entry, "reboot", problem)

    def reboot_problem(self) -> str | None:
        """Why the server isn't (yet) back after a reboot, or None if it is."""
        if not self.last_update_success or self.data is None:
            return "Server nach dem Neustart nicht erreichbar"
        before = self._boot_time_before_reboot
        if before is not None and self.data.boot_time is not None and self.data.boot_time <= before:
            return "Startzeitpunkt unverändert, der Server wurde offenbar nicht neu gestartet"
        if self.data.autostart_guests_stopped:
            guests = ", ".join(str(guest) for guest in self.data.autostart_guests_stopped)
            return f"Gäste mit Autostart laufen nicht: {guests}"
        return None

    def _async_cancel_delayed_refresh(self) -> None:
        if self._cancel_delayed_refresh is not None:
            self._cancel_delayed_refresh()
            self._cancel_delayed_refresh = None
