"""Base entity for the Server Updater integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator


class ServerUpdaterEntity(CoordinatorEntity[ServerUpdaterCoordinator]):
    """Common base entity tying platform entities to one server device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Server Updater",
            model=coordinator.data.os_name if coordinator.data else None,
        )
