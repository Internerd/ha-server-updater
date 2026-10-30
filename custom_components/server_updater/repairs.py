"""Repair flows for the Server Updater integration.

The "reboot required" issue is fixable: confirming it reboots the server
straight from Settings > Repairs. Home Assistant removes the issue once the
flow finishes; the delayed check after the reboot re-creates it only if the
server still needs a reboot.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import ServerUpdaterCoordinator
from .ssh_client import ServerUpdaterError


class RebootRepairFlow(RepairsFlow):
    """Confirm and trigger a server reboot."""

    def __init__(self, entry_id: str | None) -> None:
        self._entry_id = entry_id

    def _coordinator(self) -> ServerUpdaterCoordinator | None:
        data = self.hass.data.get(DOMAIN, {}).get(self._entry_id)
        return data["apt"] if data else None

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> data_entry_flow.FlowResult:
        coordinator = self._coordinator()
        if coordinator is None:
            return self.async_abort(reason="not_loaded")

        if user_input is not None:
            try:
                await coordinator.async_reboot()
            except ServerUpdaterError as err:
                return self.async_abort(
                    reason="reboot_failed", description_placeholders={"error": str(err)}
                )
            return self.async_create_entry(data={})

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=coordinator.reboot_issue_placeholders,
        )


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a reboot_required issue."""
    entry_id = data.get("entry_id") if data else None
    return RebootRepairFlow(str(entry_id) if entry_id else None)
