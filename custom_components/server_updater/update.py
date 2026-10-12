"""Update entities for a server's system packages and its Docker containers.

The system package entity is fixed per server. Container entities are
created and removed dynamically as the container coordinator's inventory
changes (on the initial scan and whenever the "rescan containers" button
runs) rather than being fixed at platform setup time.
"""
from __future__ import annotations

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator
from .docker_coordinator import ContainerCoordinator, ContainerUpdateStatus
from .entity import ServerUpdaterEntity
from .ssh_client import ServerUpdaterError, UpdateCheckResult

# HA truncates release_summary at 255 characters.
MAX_RELEASE_SUMMARY = 255

ATTR_IMAGE = "image"
ATTR_COMPOSE_SERVICE = "compose_service"


def _container_unique_id(entry: ConfigEntry, name: str) -> str:
    return f"{entry.entry_id}_docker_{name}"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up the system package entity and container update entities for a server."""
    async_add_entities([SystemUpdateEntity(hass.data[DOMAIN][entry.entry_id]["apt"])])

    coordinator: ContainerCoordinator = hass.data[DOMAIN][entry.entry_id]["docker"]
    known_names: set[str] = set()

    def _sync_entities() -> None:
        tracked = coordinator.tracked_names
        # Remove stale entities/devices first, so that replacements don't get
        # an entity_id suffix (_2, _3, ...) while the old one is still
        # registered.
        _remove_stale(hass, entry, {_container_unique_id(entry, n) for n in tracked})
        known_names.intersection_update(tracked)

        new_entities = [
            ContainerUpdateEntity(coordinator, entry, name)
            for name in tracked
            if name not in known_names
        ]
        known_names.update(tracked)
        if new_entities:
            async_add_entities(new_entities)

    coordinator.async_add_inventory_listener(_sync_entities)

    # Without a successful scan there is no reliable inventory to compare
    # against, so leave the registry alone until the next (manual) rescan.
    if coordinator.inventory_loaded:
        _sync_entities()


def _remove_stale(hass: HomeAssistant, entry: ConfigEntry, keep_unique_ids: set[str]) -> None:
    """Remove container entities and devices of this entry not in keep_unique_ids.

    Also cleans up leftovers from earlier versions, which keyed containers by
    their (non-stable) container ID.
    """
    prefix = f"{entry.entry_id}_docker_"

    entity_registry = er.async_get(hass)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        if (
            entity_entry.domain == "update"
            and entity_entry.unique_id.startswith(prefix)
            and entity_entry.unique_id not in keep_unique_ids
        ):
            entity_registry.async_remove(entity_entry.entity_id)

    device_registry = dr.async_get(hass)
    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        container_ids = {
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier.startswith(prefix)
        }
        if container_ids and not container_ids & keep_unique_ids:
            device_registry.async_remove_device(device.id)


class SystemUpdateEntity(ServerUpdaterEntity, UpdateEntity):
    """Pending apt package upgrades of the server itself.

    apt has no single "version" for a whole system, so the installed version
    is the OS release (e.g. "12.7" or "Proxmox VE 9.0.10") and the latest
    version adds the number of pending packages. That way the entity is "on"
    exactly while upgrades are pending, and skipping it is re-offered as soon
    as the set of pending packages changes.
    """

    _attr_translation_key = "system"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: ServerUpdaterCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.entry.entry_id}_system_update"

    @property
    def _data(self) -> UpdateCheckResult | None:
        return self.coordinator.data

    @property
    def title(self) -> str | None:
        data = self._data
        return data.os_name if data else None

    @property
    def installed_version(self) -> str | None:
        data = self._data
        if data is None:
            return None
        return data.os_version or "installiert"

    @property
    def latest_version(self) -> str | None:
        data = self._data
        installed = self.installed_version
        if data is None or installed is None:
            return None
        if data.update_count == 0:
            return installed
        noun = "Paket" if data.update_count == 1 else "Pakete"
        return f"{installed} (+{data.update_count} {noun})"

    @property
    def release_summary(self) -> str | None:
        data = self._data
        if data is None or data.update_count == 0:
            return None
        summary = ", ".join(pkg.name for pkg in data.packages)
        if len(summary) > MAX_RELEASE_SUMMARY:
            summary = summary[: MAX_RELEASE_SUMMARY - 1].rsplit(", ", 1)[0] + " …"
        return summary

    @property
    def in_progress(self) -> bool:
        return self.coordinator.busy

    async def async_release_notes(self) -> str | None:
        data = self._data
        if data is None or data.update_count == 0:
            return None
        rows = [
            f"| {pkg.name} | {pkg.current_version or '?'} | {pkg.new_version or '?'} |"
            for pkg in data.packages
        ]
        return "\n".join(
            [
                f"**{data.update_count} Paket-Update(s) verfügbar**",
                "",
                "| Paket | Installiert | Neu |",
                "|---|---|---|",
                *rows,
            ]
        )

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Run apt-get update && dist-upgrade on the server (without reboot)."""
        try:
            await self.coordinator.async_apply_updates(reboot=False)
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err


class ContainerUpdateEntity(CoordinatorEntity[ContainerCoordinator], UpdateEntity):
    """Represents one Docker container's image update status."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: ContainerCoordinator, entry: ConfigEntry, name: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._container_name = name
        self._attr_unique_id = _container_unique_id(entry, name)

    @property
    def _status(self) -> ContainerUpdateStatus | None:
        return self.coordinator.containers.get(self._container_name)

    @property
    def available(self) -> bool:
        status = self._status
        return super().available and status is not None and status.checkable

    @property
    def device_info(self) -> DeviceInfo:
        status = self._status
        model = status.container.image_ref if status else None
        return DeviceInfo(
            identifiers={(DOMAIN, _container_unique_id(self._entry, self._container_name))},
            name=self._container_name,
            manufacturer="Docker",
            model=model,
            via_device=(DOMAIN, self._entry.entry_id),
        )

    @property
    def title(self) -> str | None:
        status = self._status
        return status.container.image_ref if status else None

    @property
    def installed_version(self) -> str | None:
        status = self._status
        if status is None or not status.container.image_digest:
            return None
        return _short_digest(status.container.image_digest)

    @property
    def latest_version(self) -> str | None:
        status = self._status
        if status is None:
            return None
        if status.update_available is False:
            return self.installed_version
        return _short_digest(status.latest_digest) if status.latest_digest else None

    @property
    def release_url(self) -> str | None:
        status = self._status
        if status is None:
            return None
        return status.container.labels.get("org.opencontainers.image.source")

    @property
    def in_progress(self) -> bool:
        status = self._status
        return status is not None and status.installing

    @property
    def supported_features(self) -> UpdateEntityFeature:
        status = self._status
        if status is not None and status.compose_installable:
            return UpdateEntityFeature.INSTALL
        return UpdateEntityFeature(0)

    @property
    def extra_state_attributes(self) -> dict:
        status = self._status
        if status is None:
            return {}
        return {
            ATTR_IMAGE: status.container.image_ref,
            ATTR_COMPOSE_SERVICE: status.compose_service,
        }

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        """Pull and recreate this container via its Docker Compose service."""
        try:
            await self.coordinator.async_install_update(self._container_name)
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err


def _short_digest(digest: str) -> str:
    prefix = "sha256:"
    if digest.startswith(prefix):
        digest = digest[len(prefix) :]
    return digest[:12]
