"""Base entity and shared container helpers for the Server Updater integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
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


def container_device_id(entry: ConfigEntry, name: str) -> str:
    """Device identifier (and update entity unique ID) of one container."""
    return f"{entry.entry_id}_docker_{name}"


def container_device_info(
    entry: ConfigEntry, name: str, image_ref: str | None
) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, container_device_id(entry, name))},
        name=name,
        manufacturer="Docker",
        model=image_ref,
        via_device=(DOMAIN, entry.entry_id),
    )


def async_remove_stale_containers(
    hass: HomeAssistant,
    entry: ConfigEntry,
    domain: str,
    keep_unique_ids: set[str],
    keep_device_ids: set[str],
) -> None:
    """Remove this entry's container entities of `domain` and container devices not kept.

    Also cleans up leftovers from earlier versions, which keyed containers by
    their (non-stable) container ID.
    """
    prefix = f"{entry.entry_id}_docker_"

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if (
            entity_entry.domain == domain
            and entity_entry.unique_id.startswith(prefix)
            and entity_entry.unique_id not in keep_unique_ids
        ):
            entity_registry.async_remove(entity_entry.entity_id)

    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        container_ids = {
            identifier
            for identifier_domain, identifier in device.identifiers
            if identifier_domain == DOMAIN and identifier.startswith(prefix)
        }
        if container_ids and not container_ids & keep_device_ids:
            device_registry.async_remove_device(device.id)
