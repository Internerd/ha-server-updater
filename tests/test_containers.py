"""Container inventory, restart/stop/start, pruning and Compose updates."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.server_updater.const import DOMAIN, EVENT_SERVER_UPDATER

from .conftest import notifications, setup_server
from .fake_server import FakeContainer, FakeServer

COMPOSE_LABELS = {
    "com.docker.compose.service": "caddy",
    "com.docker.compose.project.config_files": "/srv/caddy/compose.yml",
    "com.docker.compose.project.working_dir": "/srv/caddy",
}


def _docker_server() -> FakeServer:
    fake = FakeServer()
    fake.docker = True
    fake.containers = [
        FakeContainer("caddy", "caddy:2", labels=dict(COMPOSE_LABELS)),
        FakeContainer("worker", "myapp:latest", digest=None, restart_policy="no"),
        # Stopped but meant to run: tracked.
        FakeContainer("backup", "restic:1", state="exited", restart_policy="unless-stopped"),
        # Long-exited leftover without restart policy: ignored.
        FakeContainer("oneoff", "busybox:1", state="exited", restart_policy="no"),
    ]
    fake.compose_files = {"/srv/caddy/compose.yml"}
    return fake


async def test_inventory_selection(hass, servers, registry) -> None:
    registry["caddy:2"] = "sha256:" + "a" * 64
    await setup_server(hass, servers, fake=_docker_server())

    assert hass.states.get("switch.caddy_running").state == STATE_ON
    assert hass.states.get("switch.worker_running").state == STATE_ON
    assert hass.states.get("switch.backup_running").state == STATE_OFF
    assert hass.states.get("switch.oneoff_running") is None
    assert hass.states.get("button.oneoff_restart") is None

    devices = {
        d.name for d in dr.async_entries_for_config_entry(
            dr.async_get(hass), next(iter(hass.data[DOMAIN]))
        )
    }
    assert devices == {"srv", "caddy", "worker", "backup"}


async def test_restart_stop_start(hass, servers) -> None:
    fake = _docker_server()
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)

    await hass.services.async_call("button", "press", {"entity_id": "button.worker_restart"}, blocking=True)
    assert "docker restart worker" in fake.sudo_commands

    await hass.services.async_call("switch", "turn_off", {"entity_id": "switch.worker_running"}, blocking=True)
    assert "docker stop worker" in fake.sudo_commands
    state = hass.states.get("switch.worker_running")
    assert state.state == STATE_OFF
    assert state.attributes["state"] == "exited"

    # A container stopped from Home Assistant stays in the inventory after a
    # rescan, even without a restart policy, so it can be started again.
    await hass.services.async_call("button", "press", {"entity_id": "button.srv_rescan_containers"}, blocking=True)
    state = hass.states.get("switch.worker_running")
    assert state.state == STATE_OFF
    assert state.attributes["status"] == "Exited (0) 1 minute ago"

    await hass.services.async_call("switch", "turn_on", {"entity_id": "switch.worker_running"}, blocking=True)
    assert hass.states.get("switch.worker_running").state == STATE_ON

    assert [(e.data["action"], e.data["container"], e.data["success"]) for e in events] == [
        ("container_restart", "worker", True),
        ("container_stop", "worker", True),
        ("container_start", "worker", True),
    ]


async def test_container_action_failure(hass, servers) -> None:
    fake = _docker_server()
    entry, _ = await setup_server(hass, servers, fake=fake)
    fake.failures[r"^docker restart caddy"] = (1, "Error response from daemon: boom")

    with pytest.raises(HomeAssistantError, match="boom"):
        await hass.services.async_call("button", "press", {"entity_id": "button.caddy_restart"}, blocking=True)
    assert f"{DOMAIN}_{entry.entry_id}_container_restart_caddy" in notifications(hass)
    # Entity is usable again afterwards.
    assert hass.states.get("button.caddy_restart").state != STATE_UNAVAILABLE

    del fake.failures[r"^docker restart caddy"]
    await hass.services.async_call("button", "press", {"entity_id": "button.caddy_restart"}, blocking=True)
    assert f"{DOMAIN}_{entry.entry_id}_container_restart_caddy" not in notifications(hass)


async def test_state_poll_and_unreachable(hass, servers, freezer) -> None:
    fake = _docker_server()
    await setup_server(hass, servers, fake=fake)

    # Stopped outside of Home Assistant: picked up by the regular poll.
    fake.containers[0].state = "exited"
    freezer.tick(timedelta(hours=6, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("switch.caddy_running").state == STATE_OFF

    # Unreachable: run state unknown -> switch unavailable, but no crash.
    fake.reachable = False
    freezer.tick(timedelta(hours=6, seconds=1))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()
    assert hass.states.get("switch.caddy_running").state == STATE_UNAVAILABLE


async def test_removed_container_cleans_up(hass, servers) -> None:
    fake = _docker_server()
    entry, _ = await setup_server(hass, servers, fake=fake)
    fake.containers = [c for c in fake.containers if c.name != "worker"]

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_rescan_containers"}, blocking=True)
    await hass.async_block_till_done()
    ent_reg = er.async_get(hass)
    assert ent_reg.async_get("button.worker_restart") is None
    assert ent_reg.async_get("switch.worker_running") is None
    names = {d.name for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)}
    assert "worker" not in names
    # Other containers keep their entities.
    assert ent_reg.async_get("button.caddy_restart") is not None


async def test_prune_images(hass, servers) -> None:
    fake = _docker_server()
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    await hass.services.async_call("button", "press", {"entity_id": "button.srv_prune_unused_images"}, blocking=True)
    assert "docker image prune -f" in fake.sudo_commands
    assert events[0].data["action"] == "image_prune"
    assert events[0].data["reclaimed"] == "1.2GB"


async def test_compose_update(hass, servers, registry) -> None:
    fake = _docker_server()
    registry["caddy:2"] = "sha256:" + "b" * 64
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    assert hass.states.get("update.caddy").state == STATE_ON

    await hass.services.async_call("update", "install", {"entity_id": "update.caddy"}, blocking=True)
    await hass.async_block_till_done()
    assert fake.commands_matching(r"docker compose --project-directory /srv/caddy -f /srv/caddy/compose.yml pull caddy")
    assert hass.states.get("update.caddy").state == STATE_OFF
    assert [(e.data["action"], e.data["success"]) for e in events] == [("container_update", True)]


async def test_container_devices_linked_to_server(hass, servers, registry) -> None:
    from homeassistant.helpers import device_registry as dr

    from custom_components.server_updater.const import DOMAIN

    entry, _ = await setup_server(hass, servers, fake=_docker_server())
    devices = dr.async_get(hass)
    server = devices.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
    caddy = devices.async_get_device(identifiers={(DOMAIN, f"{entry.entry_id}_docker_caddy")})
    assert caddy.via_device_id == server.id
