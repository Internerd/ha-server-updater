"""needrestart integration, package updates with reporting, and the maintenance action."""
from __future__ import annotations

import pytest
from homeassistant.const import STATE_OFF
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import async_capture_events

from custom_components.server_updater.const import DOMAIN, EVENT_SERVER_UPDATER
from custom_components.server_updater.coordinator import split_restartable_services

from .conftest import notifications, setup_server
from .fake_server import FakeContainer, FakeServer


def test_service_exclusions() -> None:
    safe, excluded = split_restartable_services([
        "ssh.service", "cron.service", "pveproxy.service", "pvedaemon.service",
        "dbus.service", "systemd-logind.service", "user@1000.service",
        "getty@tty1.service", "serial-getty@ttyS0.service", "docker.service",
        "containerd.service", "pve-guests.service", "lxc@101.service",
        "qemu-server.service", "pve-ha-lrm.service",
    ])
    assert safe == ["ssh.service", "cron.service", "pveproxy.service", "pvedaemon.service"]
    assert len(excluded) == 11


async def test_restart_services_button(hass, servers) -> None:
    fake = FakeServer()
    fake.needrestart = ["ssh.service", "cron.service", "docker.service"]
    await setup_server(hass, servers, fake=fake)
    assert hass.states.get("sensor.srv_services_to_restart").state == "3"

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_restart_services"}, blocking=True)
    assert "systemctl restart cron.service ssh.service" in fake.sudo_commands
    # Excluded service stays pending (needs a reboot).
    assert hass.states.get("sensor.srv_services_to_restart").state == "1"

    # Only an excluded service left: nothing to press (no error in the log).
    assert hass.states.get("button.srv_restart_services").state == "unavailable"


async def test_update_failure_notification_and_option(hass, servers) -> None:
    fake = FakeServer()
    fake.add_upgrade("openssl")
    fake.failures[r"dist-upgrade"] = (100, "E: Sub-process /usr/bin/dpkg returned an error code (1)")
    entry, _ = await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)

    with pytest.raises(HomeAssistantError, match="dpkg"):
        await hass.services.async_call("update", "install", {"entity_id": "update.srv_system_packages"}, blocking=True)
    assert events[-1].data["success"] is False
    assert "dpkg" in events[-1].data["error"]
    notification_id = f"{DOMAIN}_{entry.entry_id}_update"
    assert "dpkg" in notifications(hass)[notification_id]["message"]

    # Success dismisses it.
    del fake.failures[r"dist-upgrade"]
    await hass.services.async_call("update", "install", {"entity_id": "update.srv_system_packages"}, blocking=True)
    assert notification_id not in notifications(hass)
    assert events[-1].data == {
        "entry_id": entry.entry_id, "server": "srv", "action": "update", "success": True,
        "error": None, "container": None, "packages": 1,
    }


async def test_notifications_can_be_disabled(hass, servers) -> None:
    fake = FakeServer()
    fake.add_upgrade("openssl")
    fake.failures[r"dist-upgrade"] = (100, "kaputt")
    entry, _ = await setup_server(hass, servers, fake=fake, options={"notify_on_failure": False})
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call("update", "install", {"entity_id": "update.srv_system_packages"}, blocking=True)
    assert not notifications(hass)
    # The event is still fired for automations.
    assert events[-1].data["success"] is False


async def test_update_button_failure_is_reported(hass, servers) -> None:
    fake = FakeServer()
    fake.failures[r"apt-get update"] = (100, "E: Could not resolve 'deb.debian.org'")
    entry, _ = await setup_server(hass, servers, fake=fake)
    await hass.services.async_call("button", "press", {"entity_id": "button.srv_update"}, blocking=True)
    await hass.async_block_till_done()
    assert f"{DOMAIN}_{entry.entry_id}_update" in notifications(hass)
    # Not stuck busy.
    assert hass.states.get("button.srv_update").state != "unavailable"


async def test_maintenance_full_run(hass, servers, registry, freezer) -> None:
    deb = FakeServer()
    deb.add_upgrade("openssl")
    deb.add_upgrade("libc6")
    deb.update_requests_reboot = True
    deb.docker = True
    deb.containers = [FakeContainer("caddy", "caddy:2", labels={
        "com.docker.compose.service": "caddy",
        "com.docker.compose.project.config_files": "/srv/compose.yml",
    })]
    deb.compose_files = {"/srv/compose.yml"}
    registry["caddy:2"] = "sha256:" + "b" * 64
    await setup_server(hass, servers, host="deb", fake=deb)

    ubu = FakeServer()
    ubu.add_upgrade("curl")
    ubu.needrestart = ["ssh.service"]
    await setup_server(hass, servers, host="ubu", fake=ubu)

    response = await hass.services.async_call(
        DOMAIN,
        "run_maintenance",
        {"reboot": "if_required", "update_containers": True, "restart_services": True, "prune_images": True},
        blocking=True,
        return_response=True,
    )
    results = {r["server"]: r for r in response["servers"]}
    assert results["deb"] == {
        "server": "deb", "packages_updated": 2, "containers_updated": ["caddy"],
        "images_pruned": True, "services_restarted": [], "reboot_required": True,
        "rebooted": True, "error": None,
    }
    assert results["ubu"] == {
        "server": "ubu", "packages_updated": 1, "containers_updated": [],
        "images_pruned": False, "services_restarted": ["ssh.service"],
        "reboot_required": False, "rebooted": False, "error": None,
    }
    assert deb.commands_matching("^reboot$")
    assert not ubu.commands_matching("^reboot$")
    # Order on deb: packages, containers, prune, reboot last.
    order = [c for c in deb.commands if c.startswith(("apt-get -y", "docker compose", "docker image prune", "reboot"))]
    assert order[0].startswith("apt-get -y") and order[-1] == "reboot"
    assert order.index("docker image prune -f") > max(i for i, c in enumerate(order) if c.startswith("docker compose"))


async def test_maintenance_server_selection_and_reboot_modes(hass, servers) -> None:
    a, fake_a = await setup_server(hass, servers, host="a")
    b, fake_b = await setup_server(hass, servers, host="b")
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, a.entry_id)})

    response = await hass.services.async_call(
        DOMAIN, "run_maintenance", {"servers": [device.id], "reboot": "always"},
        blocking=True, return_response=True,
    )
    assert [r["server"] for r in response["servers"]] == ["a"]
    assert response["servers"][0]["rebooted"] is True
    assert fake_a.commands_matching("^reboot$") and not fake_b.commands_matching("^reboot$")

    fake_b.reboot_required_file = True
    response = await hass.services.async_call(
        DOMAIN, "run_maintenance", {"reboot": "never"}, blocking=True, return_response=True,
    )
    assert all(not r["rebooted"] for r in response["servers"])
    assert next(r for r in response["servers"] if r["server"] == "b")["reboot_required"] is True

    with pytest.raises(Exception, match="gehört zu keinem"):
        await hass.services.async_call(
            DOMAIN, "run_maintenance", {"servers": ["nope"]}, blocking=True
        )


async def test_maintenance_continues_after_failure(hass, servers) -> None:
    _, bad = await setup_server(hass, servers, host="bad")
    _, good = await setup_server(hass, servers, host="good")
    bad.add_upgrade("x")
    good.add_upgrade("y")
    bad.failures[r"dist-upgrade"] = (100, "dpkg kaputt")

    response = await hass.services.async_call(
        DOMAIN, "run_maintenance", {"reboot": "never"}, blocking=True, return_response=True
    )
    results = {r["server"]: r for r in response["servers"]}
    assert "dpkg kaputt" in results["bad"]["error"]
    assert results["good"]["error"] is None and results["good"]["packages_updated"] == 1
    assert hass.states.get("update.good_system_packages").state == STATE_OFF

    # Without a response requested, failures raise after all servers ran.
    bad.add_upgrade("x")
    with pytest.raises(HomeAssistantError, match="bad: .*dpkg kaputt"):
        await hass.services.async_call(DOMAIN, "run_maintenance", {"reboot": "never"}, blocking=True)


async def test_maintenance_unreachable_server(hass, servers) -> None:
    _, fake = await setup_server(hass, servers)
    fake.reachable = False
    response = await hass.services.async_call(
        DOMAIN, "run_maintenance", {}, blocking=True, return_response=True
    )
    assert "nicht erreichbar" in response["servers"][0]["error"]


async def test_update_sets_debian_frontend(hass, servers) -> None:
    fake = FakeServer()
    fake.add_upgrade("openssl")
    await setup_server(hass, servers, fake=fake)
    await hass.services.async_call("button", "press", {"entity_id": "button.srv_update"}, blocking=True)
    await hass.async_block_till_done()
    assert [env for cmd, env in fake.env_commands if "dist-upgrade" in cmd] == [
        {"DEBIAN_FRONTEND": "noninteractive"}
    ]
    assert fake.upgradable == []


async def test_update_works_when_sudo_rejects_env(hass, servers) -> None:
    """sudo-rs / restricted sudoers: retry without DEBIAN_FRONTEND instead of failing."""
    fake = FakeServer()
    fake.add_upgrade("openssl")
    fake.sudo_rejects_env = True
    entry, _ = await setup_server(hass, servers, fake=fake)
    await hass.services.async_call("button", "press", {"entity_id": "button.srv_update"}, blocking=True)
    await hass.async_block_till_done()
    assert fake.upgradable == []
    assert len(fake.commands_matching(r"dist-upgrade")) == 1
    assert f"{DOMAIN}_{entry.entry_id}_update" not in notifications(hass)
