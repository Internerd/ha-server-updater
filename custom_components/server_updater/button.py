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
from .coordinator import ServerUpdaterCoordinator, split_restartable_services
from .docker_coordinator import ContainerCoordinator
from .entity import (
    ContainerEntity,
    ServerUpdaterEntity,
    async_add_entities_when,
    async_track_container_entities,
    container_device_id,
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
    entities: list[ButtonEntity] = [
        UpdateButton(coordinator),
        UpdateAndRebootButton(coordinator),
        RebootButton(coordinator),
        RescanContainersButton(entry, docker_coordinator),
        PruneImagesButton(entry, docker_coordinator),
    ]
    async_add_entities(entities)
    # needrestart is optional; the button appears once it's installed.
    async_add_entities_when(
        coordinator,
        lambda: bool(coordinator.data and coordinator.data.services_to_restart is not None),
        lambda: [RestartServicesButton(coordinator)],
        async_add_entities,
    )

    # Unlike update entities, restart buttons exist for every tracked
    # container, including ones whose image can't be checked for updates.
    async_track_container_entities(
        hass,
        entry,
        docker_coordinator,
        "button",
        lambda: docker_coordinator.containers,
        lambda name: _restart_unique_id(entry, name),
        lambda name: RestartContainerButton(docker_coordinator, entry, name),
        async_add_entities,
    )


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
            # Already logged and reported (event/notification) by the coordinator.
            pass


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


class RestartServicesButton(ServerUpdaterEntity, ButtonEntity):
    """Restarts the services needrestart reports as running outdated code."""

    _attr_translation_key = "restart_services"
    _attr_icon = "mdi:cog-refresh"

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_restart_services"

    @property
    def available(self) -> bool:
        """Only pressable if there's something it may restart.

        Services such as dbus or systemd-logind are never restarted by this
        button (see SERVICE_RESTART_EXCLUDE_PATTERNS); if only those are
        pending, the server needs a reboot instead.
        """
        data = self.coordinator.data
        safe, _ = split_restartable_services(
            (data.services_to_restart if data else None) or []
        )
        return super().available and not self.coordinator.busy and bool(safe)

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_restart_services()
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err


class _ServerDockerButton(CoordinatorEntity[ContainerCoordinator], ButtonEntity):
    """Server-level Docker button.

    Shares the server device (not a per-container one) since it acts on the
    server's Docker as a whole rather than on one container.
    """

    _attr_has_entity_name = True

    def __init__(self, entry: ConfigEntry, docker_coordinator: ContainerCoordinator) -> None:
        super().__init__(docker_coordinator)
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.entry_id)})


class RescanContainersButton(_ServerDockerButton):
    """Re-discovers Docker containers on the server."""

    _attr_translation_key = "rescan_containers"
    _attr_icon = "mdi:magnify-scan"

    def __init__(self, entry: ConfigEntry, docker_coordinator: ContainerCoordinator) -> None:
        super().__init__(entry, docker_coordinator)
        self._attr_unique_id = f"{entry.entry_id}_rescan_containers"

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


class PruneImagesButton(_ServerDockerButton):
    """Removes dangling Docker images (`docker image prune -f`)."""

    _attr_translation_key = "prune_images"
    _attr_icon = "mdi:delete-sweep"

    def __init__(self, entry: ConfigEntry, docker_coordinator: ContainerCoordinator) -> None:
        super().__init__(entry, docker_coordinator)
        self._attr_unique_id = f"{entry.entry_id}_prune_images"

    @property
    def available(self) -> bool:
        # Not tied to the registry checks: pruning only needs SSH + docker.
        return bool(self.coordinator.docker_available) and not self.coordinator.scanning

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_prune_images()
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err


class RestartContainerButton(ContainerEntity, ButtonEntity):
    """Restarts one Docker container (`docker restart`)."""

    _attr_translation_key = "restart_container"
    _attr_device_class = ButtonDeviceClass.RESTART

    def __init__(
        self, coordinator: ContainerCoordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator, entry, name)
        self._attr_unique_id = _restart_unique_id(entry, name)

    @property
    def available(self) -> bool:
        # Deliberately not tied to the coordinator's last registry check:
        # restarting works over SSH even if the registry is unreachable.
        status = self._status
        return (
            status is not None
            and status.operation is None
            and not status.installing
            and not self.coordinator.scanning
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.async_container_action(self._container_name, "restart")
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err
