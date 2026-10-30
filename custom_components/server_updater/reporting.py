"""Outcome reporting: Home Assistant events and failure notifications.

Every action (package update, reboot, container and service operations)
fires a `server_updater_event` with its outcome, so automations can react
to it (e.g. push a failure to a phone). Failures additionally raise a
persistent notification unless disabled in the options; a later success of
the same action on the same target dismisses it again.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    CONF_NOTIFY_ON_FAILURE,
    DEFAULT_NOTIFY_ON_FAILURE,
    DOMAIN,
    EVENT_SERVER_UPDATER,
)

ACTION_LABELS = {
    "update": "Paket-Update",
    "reboot": "Neustart",
    "services_restart": "Neustart der Dienste",
    "container_update": "Container-Update",
    "container_restart": "Container-Neustart",
    "container_stop": "Container stoppen",
    "container_start": "Container starten",
    "image_prune": "Aufräumen der Docker-Images",
}


def async_report(
    hass: HomeAssistant,
    entry: ConfigEntry,
    action: str,
    error: str | None = None,
    container: str | None = None,
    **data: Any,
) -> None:
    """Fire the outcome event and create/dismiss the failure notification."""
    hass.bus.async_fire(
        EVENT_SERVER_UPDATER,
        {
            "entry_id": entry.entry_id,
            "server": entry.title,
            "action": action,
            "success": error is None,
            "error": error,
            "container": container,
            **data,
        },
    )

    notification_id = f"{DOMAIN}_{entry.entry_id}_{action}"
    if container:
        notification_id += f"_{container}"

    if error is None:
        persistent_notification.async_dismiss(hass, notification_id)
        return
    if not entry.options.get(CONF_NOTIFY_ON_FAILURE, DEFAULT_NOTIFY_ON_FAILURE):
        return

    target = f"Container **{container}** auf **{entry.title}**" if container else f"**{entry.title}**"
    persistent_notification.async_create(
        hass,
        f"{ACTION_LABELS.get(action, action)} für {target} fehlgeschlagen:\n\n{error}",
        title="Server Updater",
        notification_id=notification_id,
    )
