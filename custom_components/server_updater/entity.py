"""Base entity and shared container helpers for the Server Updater integration."""
from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator
from .docker_coordinator import ContainerCoordinator, ContainerUpdateStatus


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


def async_add_entities_when(
    coordinator: ServerUpdaterCoordinator,
    condition: Callable[[], bool],
    factory: Callable[[], list[Entity]],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add entities now if condition holds, otherwise as soon as it does.

    For optional features detected on the server (e.g. needrestart being
    installed later), so their entities show up without reloading.
    """
    if condition():
        async_add_entities(factory())
        return

    remove: Callable[[], None] | None = None

    @callback
    def _async_check() -> None:
        nonlocal remove
        if remove is None or not condition():
            return
        remove()
        remove = None
        async_add_entities(factory())

    @callback
    def _async_unload() -> None:
        nonlocal remove
        if remove is not None:
            remove()
            remove = None

    remove = coordinator.async_add_listener(_async_check)
    coordinator.entry.async_on_unload(_async_unload)


def container_device_id(entry: ConfigEntry, name: str) -> str:
    """Device identifier (and update entity unique ID) of one container."""
    return f"{entry.entry_id}_docker_{name}"


# Home Assistant 2026.12 replaced DeviceInfo's via_device (identifier tuple)
# with via_device_id (device registry ID); via_device stops working in
# 2027.8. Older versions only know via_device.
_HAS_VIA_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__dataclass_fields__", {})


def container_device_info(
    hass: HomeAssistant, entry: ConfigEntry, name: str, image_ref: str | None
) -> DeviceInfo:
    info: dict = {
        "identifiers": {(DOMAIN, container_device_id(entry, name))},
        "name": name,
        "manufacturer": "Docker",
        "model": image_ref,
    }
    if _HAS_VIA_DEVICE_ID:
        server = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        if server is not None:
            info["via_device_id"] = server.id
    else:
        info["via_device"] = (DOMAIN, entry.entry_id)
    return DeviceInfo(**info)


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


def async_track_container_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: ContainerCoordinator,
    domain: str,
    names: Callable[[], Iterable[str]],
    unique_id: Callable[[str], str],
    factory: Callable[[str], Entity],
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add/remove one entity per container as the inventory changes.

    `names` selects the containers that get an entity of this platform.
    Stale entities and devices are removed before new ones are added, so
    replacements don't get an entity_id suffix (_2, _3, ...) while the old
    one is still registered.
    """
    known: set[str] = set()

    def _sync() -> None:
        wanted = set(names())
        async_remove_stale_containers(
            hass,
            entry,
            domain,
            {unique_id(name) for name in wanted},
            {container_device_id(entry, name) for name in coordinator.containers},
        )
        known.intersection_update(wanted)
        new_entities = [factory(name) for name in sorted(wanted - known)]
        known.update(wanted)
        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_inventory_listener(_sync)
    # Without a successful scan there is no reliable inventory to compare
    # against, so leave the registry alone until the next (manual) rescan.
    if coordinator.inventory_loaded:
        _sync()


class ContainerEntity(CoordinatorEntity[ContainerCoordinator]):
    """Base for per-container entities, sharing one device per container."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ContainerCoordinator, entry: ConfigEntry, name: str) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._container_name = name

    @property
    def _status(self) -> ContainerUpdateStatus | None:
        return self.coordinator.containers.get(self._container_name)

    @property
    def device_info(self) -> DeviceInfo:
        status = self._status
        return container_device_info(
            self.hass,
            self._entry, self._container_name, status.container.image_ref if status else None
        )
