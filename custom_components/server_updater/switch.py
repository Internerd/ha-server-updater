"""Switches for the Server Updater integration: per-container run state."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .docker_coordinator import ContainerCoordinator
from .entity import ContainerEntity, async_track_container_entities, container_device_id
from .ssh_client import ServerUpdaterError

ATTR_STATE = "state"
ATTR_STATUS = "status"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up one run-state switch per tracked container."""
    coordinator: ContainerCoordinator = hass.data[DOMAIN][entry.entry_id]["docker"]
    async_track_container_entities(
        hass,
        entry,
        coordinator,
        "switch",
        lambda: coordinator.containers,
        lambda name: _running_unique_id(entry, name),
        lambda name: ContainerRunningSwitch(coordinator, entry, name),
        async_add_entities,
    )


def _running_unique_id(entry: ConfigEntry, name: str) -> str:
    return f"{container_device_id(entry, name)}_running"


class ContainerRunningSwitch(ContainerEntity, SwitchEntity):
    """On while the container runs; switching runs `docker start`/`docker stop`."""

    _attr_translation_key = "container_running"
    _attr_icon = "mdi:docker"

    def __init__(
        self, coordinator: ContainerCoordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator, entry, name)
        self._attr_unique_id = _running_unique_id(entry, name)

    @property
    def available(self) -> bool:
        status = self._status
        return (
            status is not None
            and status.state is not None
            and status.operation is None
            and not status.installing
            and not self.coordinator.scanning
        )

    @property
    def is_on(self) -> bool | None:
        status = self._status
        if status is None or status.state is None:
            return None
        return status.state in ("running", "restarting")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        status = self._status
        if status is None:
            return {}
        return {ATTR_STATE: status.state, ATTR_STATUS: status.status_text}

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_action("start")

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_action("stop")

    async def _async_action(self, action: str) -> None:
        try:
            await self.coordinator.async_container_action(self._container_name, action)
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err
