"""The server_updater.run_maintenance action.

Runs a complete maintenance pass per server, meant for a scheduled
automation ("Sundays at 3am"): package updates, optionally Compose container
updates and image cleanup, then either a reboot (always / only if required)
or - if no reboot follows - optionally restarting outdated services.
Servers are processed one after another; a failure on one server doesn't
stop the others.
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import (
    DOMAIN,
    REBOOT_ALWAYS,
    REBOOT_IF_REQUIRED,
    REBOOT_NEVER,
    SERVICE_RUN_MAINTENANCE,
)
from .coordinator import ServerUpdaterCoordinator, split_restartable_services
from .docker_coordinator import ContainerCoordinator
from .ssh_client import ServerUpdaterError

_LOGGER = logging.getLogger(__name__)

ATTR_SERVERS = "servers"
ATTR_REBOOT = "reboot"
ATTR_UPDATE_CONTAINERS = "update_containers"
ATTR_RESTART_SERVICES = "restart_services"
ATTR_PRUNE_IMAGES = "prune_images"

RUN_MAINTENANCE_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_SERVERS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_REBOOT, default=REBOOT_IF_REQUIRED): vol.In(
            [REBOOT_NEVER, REBOOT_IF_REQUIRED, REBOOT_ALWAYS]
        ),
        vol.Optional(ATTR_UPDATE_CONTAINERS, default=False): cv.boolean,
        vol.Optional(ATTR_RESTART_SERVICES, default=False): cv.boolean,
        vol.Optional(ATTR_PRUNE_IMAGES, default=False): cv.boolean,
    }
)


def async_setup_services(hass: HomeAssistant) -> None:
    """Register the integration's actions."""

    async def _async_run_maintenance(call: ServiceCall) -> ServiceResponse:
        entry_ids = _resolve_entry_ids(hass, call.data.get(ATTR_SERVERS))
        results = [
            await _async_maintain(hass, entry_id, call.data) for entry_id in entry_ids
        ]
        failed = [result for result in results if result["error"]]
        if failed and not call.return_response:
            raise HomeAssistantError(
                "Wartung fehlgeschlagen: "
                + "; ".join(f"{result['server']}: {result['error']}" for result in failed)
            )
        return {"servers": results} if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_RUN_MAINTENANCE,
        _async_run_maintenance,
        schema=RUN_MAINTENANCE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )


def _resolve_entry_ids(hass: HomeAssistant, device_ids: list[str] | None) -> list[str]:
    """Map selected devices (server or container devices) to loaded config entries.

    Without a selection, all loaded servers are maintained.
    """
    loaded = hass.data.get(DOMAIN, {})
    if not device_ids:
        return list(loaded)

    registry = dr.async_get(hass)
    entry_ids: list[str] = []
    for device_id in device_ids:
        device = registry.async_get(device_id)
        matches = [e for e in device.config_entries if e in loaded] if device else []
        if not matches:
            raise ServiceValidationError(
                f"Gerät {device_id} gehört zu keinem geladenen Server von Server Updater"
            )
        entry_ids.extend(e for e in matches if e not in entry_ids)
    return entry_ids


async def _async_maintain(
    hass: HomeAssistant, entry_id: str, options: dict[str, Any]
) -> dict[str, Any]:
    data = hass.data[DOMAIN][entry_id]
    apt: ServerUpdaterCoordinator = data["apt"]
    docker: ContainerCoordinator = data["docker"]
    reboot_mode = options.get(ATTR_REBOOT, REBOOT_IF_REQUIRED)

    result: dict[str, Any] = {
        "server": apt.entry.title,
        "packages_updated": 0,
        "containers_updated": [],
        "images_pruned": False,
        "services_restarted": [],
        "reboot_required": False,
        "rebooted": False,
        "error": None,
    }
    try:
        # Start from the current state rather than the last (possibly hours
        # old) poll.
        await _async_refresh(apt)
        if apt.data.update_count:
            count = apt.data.update_count
            await apt.async_apply_updates(reboot=False)
            result["packages_updated"] = count

        if options.get(ATTR_UPDATE_CONTAINERS):
            pending = [
                name
                for name, status in docker.containers.items()
                if status.update_available and status.compose_installable
            ]
            for name in pending:
                await docker.async_install_update(name)
                result["containers_updated"].append(name)

        if options.get(ATTR_PRUNE_IMAGES) and docker.docker_available:
            await docker.async_prune_images()
            result["images_pruned"] = True

        await _async_refresh(apt)
        result["reboot_required"] = apt.data.any_reboot_required
        reboot = reboot_mode == REBOOT_ALWAYS or (
            reboot_mode == REBOOT_IF_REQUIRED and apt.data.any_reboot_required
        )

        # A reboot restarts everything anyway.
        if options.get(ATTR_RESTART_SERVICES) and not reboot and apt.data.services_to_restart:
            safe, _excluded = split_restartable_services(apt.data.services_to_restart)
            if safe:
                result["services_restarted"] = await apt.async_restart_services()

        if reboot:
            await apt.async_reboot()
            result["rebooted"] = True
    except ServerUpdaterError as err:
        _LOGGER.error("Wartung von %s fehlgeschlagen: %s", apt.entry.title, err)
        result["error"] = str(err)
    return result


async def _async_refresh(coordinator: ServerUpdaterCoordinator) -> None:
    await coordinator.async_refresh()
    if not coordinator.last_update_success or coordinator.data is None:
        raise ServerUpdaterError(
            f"Server nicht erreichbar: {coordinator.last_exception or 'unbekannter Fehler'}"
        )
