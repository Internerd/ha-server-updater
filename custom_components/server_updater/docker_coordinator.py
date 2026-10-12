"""Coordinator for Docker container inventory and update checks.

Discovering containers (SSH: docker ps + docker inspect) only happens when
explicitly triggered (async_rescan, wired to a button entity and to the
initial setup) rather than on every poll. The regular coordinator refresh
only re-checks the already-known containers' images against their
registries, which is cheap and doesn't touch the server over SSH at all.

Containers are keyed by name rather than by container ID: recreating a
container (docker compose up after a pull, --force-recreate, ...) gives it
a new ID but keeps its name, and the entity for it should survive that.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import DOMAIN, REGISTRY_TIMEOUT
from .docker_registry import async_check_for_update
from .ssh_client import DockerContainerInfo, ServerConnection, ServerUpdaterError

_LOGGER = logging.getLogger(__name__)

# Labels Docker Compose (v2) sets automatically on containers it creates.
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"


@dataclass
class ContainerUpdateStatus:
    """Current known state of one container's update status."""

    container: DockerContainerInfo
    checkable: bool = False
    update_available: bool | None = None
    latest_digest: str | None = None
    compose_service: str | None = None
    compose_config_files: list[str] = field(default_factory=list)
    compose_working_dir: str | None = None
    compose_installable: bool = False
    installing: bool = False
    # Set once the container's image update could be determined at least
    # once; only tracked containers get an update entity.
    tracked: bool = False


def _extract_compose_info(container: DockerContainerInfo) -> tuple[str | None, list[str], str | None]:
    service = container.labels.get(COMPOSE_SERVICE_LABEL)
    raw_config_files = container.labels.get(COMPOSE_CONFIG_FILES_LABEL)
    config_files = [f for f in raw_config_files.split(",") if f] if raw_config_files else []
    working_dir = container.labels.get(COMPOSE_WORKING_DIR_LABEL)
    return service, config_files, working_dir


class ContainerCoordinator(DataUpdateCoordinator[dict[str, ContainerUpdateStatus]]):
    """Coordinates Docker container inventory and update checks for a server."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        connection_factory: Callable[[], ServerConnection],
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}_docker",
            update_interval=update_interval,
        )
        self.entry = entry
        self._connection_factory = connection_factory
        self.containers: dict[str, ContainerUpdateStatus] = {}
        self._inventory_listeners: list[Callable[[], None]] = []
        self.scanning = False
        self.inventory_loaded = False

    @property
    def tracked_names(self) -> list[str]:
        """Names of containers that should have an update entity."""
        return [name for name, status in self.containers.items() if status.tracked]

    def async_add_inventory_listener(self, listener: Callable[[], None]) -> None:
        """Register a callback invoked after every successful inventory scan."""
        self._inventory_listeners.append(listener)

    async def async_rescan(self) -> None:
        """Re-discover containers on the server and refresh their update status."""
        self.scanning = True
        self.async_update_listeners()
        try:
            conn = self._connection_factory()
            async with conn:
                discovered = await conn.async_list_docker_containers()

                discovered_names = {container.name for container in discovered}
                for name in [n for n in self.containers if n not in discovered_names]:
                    self.containers.pop(name, None)

                for container in discovered:
                    service, config_files, working_dir = _extract_compose_info(container)
                    compose_installable = bool(
                        service and config_files
                    ) and await conn.async_check_paths_exist(config_files)

                    existing = self.containers.get(container.name)
                    if existing is None:
                        existing = ContainerUpdateStatus(container=container)
                        self.containers[container.name] = existing
                    else:
                        existing.container = container
                    existing.compose_service = service
                    existing.compose_config_files = config_files
                    existing.compose_working_dir = working_dir
                    existing.compose_installable = compose_installable

            await self._async_check_all()

            for status in self.containers.values():
                if status.checkable:
                    status.tracked = True

            self.inventory_loaded = True
            for listener in self._inventory_listeners:
                listener()

            self.async_set_updated_data(self.containers)
        except ServerUpdaterError:
            _LOGGER.warning(
                "Docker-Inventarisierung für %s fehlgeschlagen", self.entry.title
            )
            raise
        finally:
            self.scanning = False
            self.async_update_listeners()

    async def async_install_update(self, name: str) -> None:
        """Pull and recreate one container via its recorded Docker Compose service."""
        status = self.containers.get(name)
        if status is None:
            raise ServerUpdaterError(
                "Container nicht mehr im Inventar bekannt, bitte neu inventarisieren"
            )
        if not status.compose_installable or not status.compose_service:
            raise ServerUpdaterError(
                "Für diesen Container ist keine automatische Aktualisierung möglich"
            )

        status.installing = True
        self.async_update_listeners()
        try:
            conn = self._connection_factory()
            async with conn:
                await conn.async_apply_compose_update(
                    status.compose_config_files,
                    status.compose_service,
                    status.compose_working_dir,
                )
        finally:
            status.installing = False
            self.async_update_listeners()

        await self.async_rescan()

    async def _async_update_data(self) -> dict[str, ContainerUpdateStatus]:
        await self._async_check_all()
        return self.containers

    async def _async_check_all(self) -> None:
        if not self.containers:
            return
        session = async_get_clientsession(self.hass)
        for status in self.containers.values():
            result = await async_check_for_update(
                session,
                status.container.image_ref,
                status.container.image_digest,
                timeout=REGISTRY_TIMEOUT,
            )
            if result is None:
                status.checkable = False
                status.update_available = None
                status.latest_digest = None
            else:
                status.checkable = True
                status.update_available, status.latest_digest = result
