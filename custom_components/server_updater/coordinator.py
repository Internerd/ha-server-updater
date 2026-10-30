"""DataUpdateCoordinator for the Server Updater integration."""
from __future__ import annotations

import logging
from collections.abc import Callable
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

from .const import (
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
)
from .ssh_client import ServerConnection, ServerUpdaterError, UpdateCheckResult

_LOGGER = logging.getLogger(__name__)


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
        self.reboot_issue_placeholders: dict[str, str] = {}
        self._cancel_delayed_refresh: Callable[[], None] | None = None
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
        )

    async def _async_update_data(self) -> UpdateCheckResult:
        try:
            async with self.build_connection() as conn:
                result = await conn.async_check_updates()
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

    async def async_apply_updates(self, reboot: bool) -> None:
        """Apply updates and optionally reboot, then refresh state."""
        if self.busy:
            _LOGGER.warning(
                "Update für %s läuft bereits, Anfrage wird ignoriert", self.entry.title
            )
            return

        self.busy = True
        self.async_update_listeners()
        try:
            async with self.build_connection() as conn:
                await conn.async_apply_updates()
                if reboot:
                    await conn.async_reboot()
        except ServerUpdaterError:
            _LOGGER.exception("Update für %s fehlgeschlagen", self.entry.title)
            raise
        finally:
            self.busy = False
            self.async_update_listeners()

        if reboot:
            self._async_schedule_refresh_after_reboot()
        else:
            await self.async_request_refresh()

    async def async_reboot(self) -> None:
        """Reboot the server without installing updates first."""
        if self.busy:
            raise ServerUpdaterError(
                f"Auf {self.entry.title} läuft bereits ein Vorgang, bitte später erneut versuchen"
            )

        self.busy = True
        self.async_update_listeners()
        try:
            async with self.build_connection() as conn:
                await conn.async_reboot()
        except ServerUpdaterError:
            _LOGGER.exception("Neustart von %s fehlgeschlagen", self.entry.title)
            raise
        finally:
            self.busy = False
            self.async_update_listeners()

        _LOGGER.info("Neustart von %s ausgelöst", self.entry.title)
        self._async_schedule_refresh_after_reboot()

    def _async_schedule_refresh_after_reboot(self) -> None:
        """Re-check the server once it had time to come back up.

        Refreshing right away would hit the shutting-down server and either
        fail or still report the pre-reboot state.
        """
        self._async_cancel_delayed_refresh()

        async def _async_refresh(_now: datetime) -> None:
            self._cancel_delayed_refresh = None
            await self.async_request_refresh()

        self._cancel_delayed_refresh = async_call_later(
            self.hass, REBOOT_REFRESH_DELAY, _async_refresh
        )

    def _async_cancel_delayed_refresh(self) -> None:
        if self._cancel_delayed_refresh is not None:
            self._cancel_delayed_refresh()
            self._cancel_delayed_refresh = None
