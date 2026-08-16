"""Update entities for Docker containers with an auto-detectable image update.

Entities are created and removed dynamically as the container coordinator's
inventory changes (on the initial scan and whenever the "rescan containers"
button runs) rather than being fixed at platform setup time.
"""
from __future__ import annotations

from homeassistant.components.update import UpdateEntity, UpdateEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .docker_coordinator import ContainerCoordinator, ContainerUpdateStatus
from .ssh_client import ServerUpdaterError

ATTR_IMAGE = "image"
ATTR_COMPOSE_SERVICE = "compose_service"


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up (dynamically maintained) container update entities for a server."""
    coordinator: ContainerCoordinator = hass.data[DOMAIN][entry.entry_id]["docker"]
    known_ids: set[str] = set()

    def _add_containers(container_ids: list[str]) -> None:
        new_entities = [
            ContainerUpdateEntity(coordinator, entry, container_id)
            for container_id in container_ids
            if container_id not in known_ids
        ]
        known_ids.update(container_ids)
        if new_entities:
            async_add_entities(new_entities)

    def _remove_containers(container_ids: list[str]) -> None:
        registry = er.async_get(hass)
        for container_id in container_ids:
            known_ids.discard(container_id)
            unique_id = f"{entry.entry_id}_docker_{container_id}"
            if entity_id := registry.async_get_entity_id("update", DOMAIN, unique_id):
                registry.async_remove(entity_id)

    coordinator.async_add_containers_added_listener(_add_containers)
    coordinator.async_add_containers_removed_listener(_remove_containers)

    if coordinator.containers:
        _add_containers(list(coordinator.containers))


class ContainerUpdateEntity(CoordinatorEntity[ContainerCoordinator], UpdateEntity):
    """Represents one Docker container's image update status."""

    _attr_has_entity_name = True
    _attr_name = None

    def __init__(
        self, coordinator: ContainerCoordinator, entry: ConfigEntry, container_id: str
    ) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._container_id = container_id
        self._attr_unique_id = f"{entry.entry_id}_docker_{container_id}"

    @property
    def _status(self) -> ContainerUpdateStatus | None:
        return self.coordinator.containers.get(self._container_id)

    @property
    def available(self) -> bool:
        status = self._status
        return super().available and status is not None and status.checkable

    @property
    def device_info(self) -> DeviceInfo:
        status = self._status
        name = status.container.name if status else self._container_id
        model = status.container.image_ref if status else None
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._entry.entry_id}_docker_{self._container_id}")},
            name=name,
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
            await self.coordinator.async_install_update(self._container_id)
        except ServerUpdaterError as err:
            raise HomeAssistantError(str(err)) from err


def _short_digest(digest: str) -> str:
    prefix = "sha256:"
    if digest.startswith(prefix):
        digest = digest[len(prefix) :]
    return digest[:12]
