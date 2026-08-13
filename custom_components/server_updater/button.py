"""Buttons for the Server Updater integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator
from .docker_coordinator import ContainerCoordinator
from .entity import ServerUpdaterEntity
from .ssh_client import ServerUpdaterError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up buttons for a server."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: ServerUpdaterCoordinator = data["apt"]
    docker_coordinator: ContainerCoordinator = data["docker"]
    async_add_entities(
        [
            UpdateButton(coordinator),
            UpdateAndRebootButton(coordinator),
            RescanContainersButton(entry, docker_coordinator),
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


class RescanContainersButton(CoordinatorEntity[ContainerCoordinator], ButtonEntity):
    """Re-discovers Docker containers running on the server.

    Shares the server device (not a per-container one) since it drives the
    inventory itself rather than representing one container.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "rescan_containers"
    _attr_icon = "mdi:magnify-scan"

    def __init__(self, entry: ConfigEntry, docker_coordinator: ContainerCoordinator) -> None:
        super().__init__(docker_coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rescan_containers"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.scanning

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_rescan()
        except ServerUpdaterError:
            _LOGGER.error(
                "Container-Inventarisierung für %s fehlgeschlagen, siehe vorheriges Log",
                self.coordinator.entry.title,
            )
