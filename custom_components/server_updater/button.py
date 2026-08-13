"""Buttons for the Server Updater integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator
from .entity import ServerUpdaterEntity
from .ssh_client import ServerUpdaterError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up buttons for a server."""
    coordinator: ServerUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            UpdateButton(coordinator),
            UpdateAndRebootButton(coordinator),
        ]
    )


class _BaseUpdateButton(ServerUpdaterEntity, ButtonEntity):
    """Shared press handling for the update buttons."""

    _reboot = False

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.busy

    async def async_press(self) -> None:
        # Runs as a background task so the button press returns immediately
        # instead of blocking on a potentially long apt upgrade / reboot.
        self.hass.async_create_task(self._async_run())

    async def _async_run(self) -> None:
        try:
            await self.coordinator.async_apply_updates(reboot=self._reboot)
        except ServerUpdaterError:
            _LOGGER.error(
                "Update-Vorgang für %s fehlgeschlagen, siehe vorheriges Log",
                self.coordinator.entry.title,
            )


class UpdateButton(_BaseUpdateButton):
    """Applies available updates without rebooting."""

    _attr_translation_key = "update"
    _attr_device_class = ButtonDeviceClass.UPDATE
    _reboot = False

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_update"


class UpdateAndRebootButton(_BaseUpdateButton):
    """Applies available updates and reboots the server afterwards."""

    _attr_translation_key = "update_and_reboot"
    _attr_icon = "mdi:restart"
    _reboot = True

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_update_and_reboot"
