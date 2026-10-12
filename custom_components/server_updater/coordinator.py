"""DataUpdateCoordinator for the Server Updater integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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
        """Show a pending reboot under Settings > Repairs, alongside other system notices."""
        if not result.reboot_required:
            ir.async_delete_issue(self.hass, DOMAIN, self.reboot_issue_id)
            return

        packages = result.reboot_required_packages
        package_text = ", ".join(packages[:MAX_REBOOT_PACKAGES_IN_ISSUE])
        if len(packages) > MAX_REBOOT_PACKAGES_IN_ISSUE:
            package_text += f" (+{len(packages) - MAX_REBOOT_PACKAGES_IN_ISSUE})"
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.reboot_issue_id,
            is_fixable=False,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="reboot_required",
            translation_placeholders={
                "name": self.entry.title,
                "packages": package_text or "-",
            },
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

        await self.async_request_refresh()
