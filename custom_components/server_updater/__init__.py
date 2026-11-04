"""The Server Updater integration."""
from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.typing import ConfigType

from .config_flow import OPTIONS_FLOW_RELOADS
from .const import CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import ServerUpdaterCoordinator
from .docker_coordinator import ContainerCoordinator
from .services import async_setup_services
from .ssh_client import ServerUpdaterError

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's actions (independent of any server)."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Server Updater from a config entry."""
    _async_remove_legacy_entities(hass, entry)

    scan_interval_minutes = entry.options.get(CONF_SCAN_INTERVAL_MINUTES)
    update_interval = (
        timedelta(minutes=scan_interval_minutes)
        if scan_interval_minutes
        else DEFAULT_SCAN_INTERVAL
    )

    # Register the server device up front: container devices link to it by
    # its registry ID, which must exist before any platform adds entities.
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="Server Updater",
    )

    coordinator = ServerUpdaterCoordinator(hass, entry, update_interval)
    await coordinator.async_config_entry_first_refresh()

    docker_coordinator = ContainerCoordinator(
        hass, entry, coordinator.build_connection, update_interval
    )
    try:
        await docker_coordinator.async_rescan()
    except ServerUpdaterError:
        # Docker support is best-effort: a server without Docker (or with a
        # different SSH setup for it) shouldn't block the rest of the
        # integration from loading. The user can retry via the rescan
        # button once the underlying issue is fixed.
        _LOGGER.debug(
            "Erste Docker-Inventarisierung für %s fehlgeschlagen, wird ohne Container fortgesetzt",
            entry.title,
        )

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "apt": coordinator,
        "docker": docker_coordinator,
        # Older Home Assistant only: to tell option changes (reload) from
        # data changes such as pinning the host key (no reload needed).
        "options": dict(entry.options),
    }

    if not OPTIONS_FLOW_RELOADS:
        # Newer Home Assistant reloads after option changes by itself
        # (OptionsFlowWithReload) and warns about update listeners when a
        # reconfigure/reauth flow reloads the entry.
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        data = hass.data[DOMAIN].pop(entry.entry_id, None)
        if data is not None:
            ir.async_delete_issue(hass, DOMAIN, data["apt"].reboot_issue_id)
    return unload_ok


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clean up a removed server's repair issue."""
    ir.async_delete_issue(hass, DOMAIN, f"reboot_required_{entry.entry_id}")


def _async_remove_legacy_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Drop registry entries of entities that no longer exist.

    binary_sensor.<server>_updates_available was replaced by the system
    package update entity in 0.4.0.
    """
    registry = er.async_get(hass)
    if entity_id := registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{entry.entry_id}_updates_available"
    ):
        registry.async_remove(entity_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    data = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if data is not None and data.get("options") == dict(entry.options):
        return
    await hass.config_entries.async_reload(entry.entry_id)
