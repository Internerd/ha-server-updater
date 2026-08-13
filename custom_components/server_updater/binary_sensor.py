"""Binary sensors for the Server Updater integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_OS_NAME,
    ATTR_PACKAGES,
    ATTR_REBOOT_PACKAGES,
    ATTR_UPDATE_COUNT,
    DOMAIN,
    MAX_PACKAGE_ATTRIBUTE,
)
from .coordinator import ServerUpdaterCoordinator
from .entity import ServerUpdaterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensors for a server."""
    coordinator: ServerUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            UpdatesAvailableBinarySensor(coordinator),
            RebootRequiredBinarySensor(coordinator),
        ]
    )


class UpdatesAvailableBinarySensor(ServerUpdaterEntity, BinarySensorEntity):
    """Indicates whether package updates are available."""

    _attr_translation_key = "updates_available"
    _attr_device_class = BinarySensorDeviceClass.UPDATE

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_updates_available"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.update_count > 0

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            ATTR_UPDATE_COUNT: data.update_count,
            ATTR_PACKAGES: data.packages[:MAX_PACKAGE_ATTRIBUTE],
            ATTR_OS_NAME: data.os_name,
        }


class RebootRequiredBinarySensor(ServerUpdaterEntity, BinarySensorEntity):
    """Indicates whether the server needs a reboot."""

    _attr_translation_key = "reboot_required"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reboot_required"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.reboot_required

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            ATTR_REBOOT_PACKAGES: data.reboot_required_packages[:MAX_PACKAGE_ATTRIBUTE],
        }
