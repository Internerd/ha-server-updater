"""The Server Updater integration."""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_SCAN_INTERVAL_MINUTES, DEFAULT_SCAN_INTERVAL, DOMAIN, PLATFORMS
from .coordinator import ServerUpdaterCoordinator


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Server Updater from a config entry."""
    scan_interval_minutes = entry.options.get(CONF_SCAN_INTERVAL_MINUTES)
    update_interval = (
        timedelta(minutes=scan_interval_minutes)
        if scan_interval_minutes
        else DEFAULT_SCAN_INTERVAL
    )

    coordinator = ServerUpdaterCoordinator(hass, entry, update_interval)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
