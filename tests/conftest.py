"""Fixtures: Home Assistant test harness plus simulated servers."""
from __future__ import annotations

from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.server_updater.const import DOMAIN
from custom_components.server_updater.ssh_client import (
    ServerConnection,
    ServerConnectionError,
)

from .fake_server import FakeServer

pytest_plugins = "pytest_homeassistant_custom_component"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture
def servers() -> dict[str, FakeServer]:
    """Simulated servers by host; tests add to it before setting up entries."""
    return {}


@pytest.fixture
def registry() -> dict[str, str]:
    """Latest registry digest per image ref; missing = not checkable."""
    return {}


@pytest.fixture(autouse=True)
def patch_ssh(servers, registry):
    async def _connect(self):
        server = servers[self._host]
        if not server.reachable:
            raise ServerConnectionError(f"Zeitüberschreitung beim Verbinden mit {self._host}:22")
        self._conn = object()

    async def _close(self):
        self._conn = None

    async def _run(self, command, use_sudo=False, timeout=None):
        return servers[self._host].run(command, use_sudo=use_sudo)

    async def _check(session, image_ref, current_digest, timeout):
        latest = registry.get(image_ref)
        if latest is None:
            return None
        return latest != current_digest, latest

    with (
        patch.object(ServerConnection, "_connect", _connect),
        patch.object(ServerConnection, "close", _close),
        patch.object(ServerConnection, "_run", _run),
        patch(
            "custom_components.server_updater.docker_coordinator.async_check_for_update",
            _check,
        ),
    ):
        yield


async def setup_server(hass, servers, host="srv", fake=None, title=None, options=None):
    """Add a simulated server and set up its config entry."""
    fake = fake or FakeServer()
    servers[host] = fake
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=title or host,
        data={"host": host, "port": 22, "username": "ha", "use_sudo": True},
        options=options or {},
        unique_id=f"{host}:22:ha",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, fake


def notifications(hass) -> dict:
    """Currently shown persistent notifications by ID."""
    from homeassistant.components.persistent_notification import (
        _async_get_or_create_notifications,
    )

    return _async_get_or_create_notifications(hass)
