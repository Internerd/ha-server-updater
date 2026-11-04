"""Sensors for the Server Updater integration."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ATTR_EXCLUDED_SERVICES, ATTR_SERVICES, DOMAIN
from .coordinator import ServerUpdaterCoordinator, split_restartable_services
from .entity import ServerUpdaterEntity, async_add_entities_when


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up sensors for a server."""
    coordinator: ServerUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id]["apt"]
    async_add_entities([LastBootSensor(coordinator)])
    # needrestart is optional; the sensor appears once it's installed.
    async_add_entities_when(
        coordinator,
        lambda: bool(coordinator.data and coordinator.data.services_to_restart is not None),
        lambda: [ServicesToRestartSensor(coordinator)],
        async_add_entities,
    )


class LastBootSensor(ServerUpdaterEntity, SensorEntity):
    """When the server was last booted."""

    _attr_translation_key = "last_boot"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_last_boot"

    @property
    def native_value(self) -> datetime | None:
        data = self.coordinator.data
        if data is None or data.boot_time is None:
            return None
        return datetime.fromtimestamp(data.boot_time, tz=timezone.utc)


class ServicesToRestartSensor(ServerUpdaterEntity, SensorEntity):
    """Number of services running outdated binaries/libraries (needrestart)."""

    _attr_translation_key = "services_to_restart"
    _attr_icon = "mdi:cog-refresh"

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_services_to_restart"

    @property
    def native_value(self) -> int | None:
        data = self.coordinator.data
        if data is None or data.services_to_restart is None:
            return None
        return len(data.services_to_restart)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None or data.services_to_restart is None:
            return {}
        safe, excluded = split_restartable_services(data.services_to_restart)
        return {ATTR_SERVICES: safe, ATTR_EXCLUDED_SERVICES: excluded}
