"""Buttons for the Server Updater integration."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator
from .docker_coordinator import ContainerCoordinator
from .entity import (
    ServerUpdaterEntity,
    async_remove_stale_containers,
    container_device_id,
    container_device_info,
)
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
            RebootButton(coordinator),
            RescanContainersButton(entry, docker_coordinator),
        ]
    )

    # Unlike update entities, restart buttons exist for every running
    # container, including ones whose image can't be checked for updates.
    known_names: set[str] = set()

    def _sync_restart_buttons() -> None:
        names = set(docker_coordinator.containers)
        async_remove_stale_containers(
            hass,
            entry,
            "button",
            {_restart_unique_id(entry, n) for n in names},
            {container_device_id(entry, n) for n in names},
        )
        known_names.intersection_update(names)

        new_entities = [
            RestartContainerButton(docker_coordinator, entry, name)
            for name in sorted(names - known_names)
        ]
        known_names.update(names)
        if new_entities:
            async_add_entities(new_entities)

    docker_coordinator.async_add_inventory_listener(_sync_restart_buttons)
    if docker_coordinator.inventory_loaded:
        _sync_restart_buttons()


def _restart_unique_id(entry: ConfigEntry, name: str) -> str:
    return f"{container_device_id(entry, name)}_restart"


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


class RebootButton(ServerUpdaterEntity, ButtonEntity):
    """Reboots the server without installing updates first."""

    _attr_translation_key = "reboot"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reboot"

    @property
    def available(self) -> bool:
        return super().available and not self.coordinator.busy

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_reboot()
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err


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


class RestartContainerButton(CoordinatorEntity[ContainerCoordinator], ButtonEntity):
    """Restarts one Docker container (`docker restart`)."""

    _attr_has_entity_name = True
    _attr_translation_key = "restart_container"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self, coordinator: ContainerCoordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._container_name = name
        self._attr_unique_id = _restart_unique_id(entry, name)

    @property
    def device_info(self) -> DeviceInfo:
        status = self.coordinator.containers.get(self._container_name)
        return container_device_info(
            self._entry, self._container_name, status.container.image_ref if status else None
        )

    @property
    def available(self) -> bool:
        # Deliberately not tied to the coordinator's last registry check:
        # restarting works over SSH even if the registry is unreachable.
        status = self.coordinator.containers.get(self._container_name)
        return (
            status is not None
            and not status.restarting
            and not status.installing
            and not self.coordinator.scanning
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_restart_container(self._container_name)
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err
