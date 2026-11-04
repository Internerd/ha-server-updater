"""Binary sensors for the Server Updater integration."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ATTR_GUESTS,
    ATTR_NEWEST_KERNEL,
    ATTR_REBOOT_PACKAGES,
    ATTR_REBOOTING,
    ATTR_RUNNING_KERNEL,
    ATTR_SERVICES_REQUIRING_REBOOT,
    DOMAIN,
    MAX_PACKAGE_ATTRIBUTE,
)
from .coordinator import ServerUpdaterCoordinator
from .entity import ServerUpdaterEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up binary sensors for a server.

    Available package updates are exposed as an update entity (see update.py)
    so they show up under Settings > Updates.
    """
    coordinator: ServerUpdaterCoordinator = hass.data[DOMAIN][entry.entry_id]["apt"]
    entities: list[BinarySensorEntity] = [
        RebootRequiredBinarySensor(coordinator),
        ReachableBinarySensor(coordinator),
    ]
    if coordinator.data and coordinator.data.is_proxmox:
        entities.append(AutostartGuestsStoppedBinarySensor(coordinator))
    async_add_entities(entities)


class RebootRequiredBinarySensor(ServerUpdaterEntity, BinarySensorEntity):
    """Indicates whether the server needs a reboot.

    On when /var/run/reboot-required exists, a newer kernel is installed
    than the one running (Proxmox VE doesn't flag kernel updates otherwise),
    or needrestart lists services that only a reboot updates (dbus, ...).
    """

    _attr_translation_key = "reboot_required"
    _attr_icon = "mdi:restart-alert"

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reboot_required"

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.any_reboot_required

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None:
            return {}
        return {
            ATTR_REBOOT_PACKAGES: data.reboot_required_packages[:MAX_PACKAGE_ATTRIBUTE],
            ATTR_RUNNING_KERNEL: data.running_kernel,
            ATTR_NEWEST_KERNEL: data.newest_kernel,
            ATTR_SERVICES_REQUIRING_REBOOT: data.services_requiring_reboot,
        }


class ReachableBinarySensor(ServerUpdaterEntity, BinarySensorEntity):
    """Whether the last check reached the server over SSH.

    Stays available while the server is down (unlike the other entities),
    so automations can react to it going off, e.g. after a reboot.
    """

    _attr_translation_key = "reachable"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_reachable"

    @property
    def available(self) -> bool:
        return True

    @property
    def is_on(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict:
        return {ATTR_REBOOTING: self.coordinator.rebooting}


class AutostartGuestsStoppedBinarySensor(ServerUpdaterEntity, BinarySensorEntity):
    """Proxmox VE: on while guests with "start at boot" aren't running."""

    _attr_translation_key = "autostart_guests_stopped"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_autostart_guests_stopped"

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None or data.autostart_guests_stopped is None:
            return None
        return bool(data.autostart_guests_stopped)

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        if data is None or data.autostart_guests_stopped is None:
            return {}
        return {ATTR_GUESTS: [str(guest) for guest in data.autostart_guests_stopped]}
