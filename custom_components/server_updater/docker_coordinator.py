"""Coordinator for Docker container inventory and update checks.

Discovering containers (SSH: docker ps + docker inspect) only happens when
explicitly triggered (async_rescan, wired to a button entity and to the
initial setup) rather than on every poll. The regular coordinator refresh
re-checks the already-known containers' images against their registries
and reads their run states with a single `docker ps -a` over SSH.

Running containers are always tracked. Stopped ones only if they are meant
to run (restart policy always/unless-stopped/on-failure) or were already
tracked, so a container stopped from Home Assistant can be started again
while long-exited leftovers don't each get a device.

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
from .reporting import async_report
from .ssh_client import DockerContainerInfo, ServerConnection, ServerUpdaterError

_LOGGER = logging.getLogger(__name__)

# Labels Docker Compose (v2) sets automatically on containers it creates.
COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
COMPOSE_CONFIG_FILES_LABEL = "com.docker.compose.project.config_files"
COMPOSE_WORKING_DIR_LABEL = "com.docker.compose.project.working_dir"

RESTART_POLICIES_MEANT_TO_RUN = ("always", "unless-stopped", "on-failure")


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
    # "restart", "stop" or "start" while such a docker command runs.
    operation: str | None = None
    # Run state from the last scan/poll ("running", "exited", ...) and
    # docker's human-readable status ("Up 3 hours").
    state: str | None = None
    status_text: str | None = None
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
        # None until the first scan; False if docker isn't usable on the server.
        self.docker_available: bool | None = None

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
                listed = await conn.async_list_docker_containers()
                self.docker_available = listed is not None
                discovered = [
                    container
                    for container in listed or []
                    if container.state == "running"
                    or container.restart_policy in RESTART_POLICIES_MEANT_TO_RUN
                    or container.name in self.containers
                ]

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
                    existing.state = container.state
                    existing.compose_service = service
                    existing.compose_config_files = config_files
                    existing.compose_working_dir = working_dir
                    existing.compose_installable = compose_installable

                if self.containers:
                    await self._async_read_states(conn)

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
        except ServerUpdaterError as err:
            async_report(self.hass, self.entry, "container_update", str(err), container=name)
            raise
        finally:
            status.installing = False
            self.async_update_listeners()

        async_report(self.hass, self.entry, "container_update", container=name)
        await self.async_rescan()

    async def async_container_action(self, name: str, action: str) -> None:
        """Run `docker restart|stop|start` for one container, then refresh its state.

        Restarting keeps the container's ID and configuration, so no rescan
        is needed.
        """
        status = self.containers.get(name)
        if status is None:
            raise ServerUpdaterError(
                "Container nicht mehr im Inventar bekannt, bitte neu inventarisieren"
            )

        status.operation = action
        self.async_update_listeners()
        try:
            conn = self._connection_factory()
            async with conn:
                await conn.async_container_action(action, name)
                await self._async_read_states(conn)
        except ServerUpdaterError as err:
            async_report(self.hass, self.entry, f"container_{action}", str(err), container=name)
            raise
        finally:
            status.operation = None
            self.async_update_listeners()

        async_report(self.hass, self.entry, f"container_{action}", container=name)

    async def async_prune_images(self) -> str | None:
        """Remove dangling Docker images; returns the reclaimed space, if reported."""
        try:
            conn = self._connection_factory()
            async with conn:
                reclaimed = await conn.async_prune_images()
        except ServerUpdaterError as err:
            async_report(self.hass, self.entry, "image_prune", str(err))
            raise
        _LOGGER.info(
            "Docker-Images auf %s aufgeräumt, freigegeben: %s",
            self.entry.title,
            reclaimed or "unbekannt",
        )
        async_report(self.hass, self.entry, "image_prune", reclaimed=reclaimed)
        return reclaimed

    async def _async_read_states(self, conn: ServerConnection) -> None:
        states = await conn.async_get_container_states()
        for name, status in self.containers.items():
            status.state, status.status_text = states.get(name, ("removed", ""))

    async def _async_update_data(self) -> dict[str, ContainerUpdateStatus]:
        await self._async_check_all()
        if self.containers:
            # Best effort: an unreachable server shouldn't make the registry
            # based update entities unavailable.
            try:
                conn = self._connection_factory()
                async with conn:
                    await self._async_read_states(conn)
            except ServerUpdaterError as err:
                _LOGGER.debug(
                    "Container-Status auf %s nicht lesbar: %s", self.entry.title, err
                )
                for status in self.containers.values():
                    status.state = None
                    status.status_text = None
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
