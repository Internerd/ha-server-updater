"""Which entities a server gets depending on what is installed on it."""
from __future__ import annotations

from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE

from custom_components.server_updater.const import DOMAIN

from .conftest import setup_server
from .fake_server import FakeContainer, FakeGuest, FakeServer


async def test_plain_debian(hass, servers) -> None:
    await setup_server(hass, servers)
    ids = set(hass.states.async_entity_ids())
    assert {
        "update.srv_system_packages",
        "binary_sensor.srv_reboot_required",
        "binary_sensor.srv_reachable",
        "sensor.srv_last_boot",
        "button.srv_update",
        "button.srv_update_and_reboot",
        "button.srv_reboot",
        "button.srv_rescan_containers",
        "button.srv_prune_unused_images",
    } <= ids
    # Optional features stay hidden when not applicable.
    assert "sensor.srv_services_to_restart" not in ids
    assert "button.srv_restart_services" not in ids
    assert "binary_sensor.srv_autostart_guests_stopped" not in ids
    # No docker: pruning is not possible.
    assert hass.states.get("button.srv_prune_unused_images").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.srv_reachable").state == STATE_ON
    assert hass.states.get("binary_sensor.srv_reboot_required").state == STATE_OFF
    assert hass.states.get("sensor.srv_last_boot").state == "2023-11-14T22:13:20+00:00"
    assert hass.services.has_service(DOMAIN, "run_maintenance")


async def test_proxmox_with_needrestart_and_docker(hass, servers, registry) -> None:
    fake = FakeServer(proxmox=True)
    fake.needrestart = ["ssh.service", "docker.service"]
    fake.guests = [FakeGuest(101, "dns", status="stopped")]
    fake.docker = True
    fake.containers = [
        FakeContainer("caddy", "caddy:2"),
        FakeContainer("local", "myapp:latest", digest=None),
    ]
    registry["caddy:2"] = "sha256:" + "a" * 64
    await setup_server(hass, servers, host="pve", fake=fake)

    services = hass.states.get("sensor.pve_services_to_restart")
    assert services.state == "2"
    assert services.attributes["services"] == ["ssh.service"]
    assert services.attributes["excluded_services"] == ["docker.service"]

    guests = hass.states.get("binary_sensor.pve_autostart_guests_stopped")
    assert guests.state == STATE_ON
    assert guests.attributes["guests"] == ["VM 101 (dns)"]

    # Restart button and run switch for every container; update entity only
    # for the one whose image can be checked against a registry.
    assert hass.states.get("button.caddy_restart") is not None
    assert hass.states.get("button.local_restart") is not None
    assert hass.states.get("switch.caddy_running").state == STATE_ON
    assert hass.states.get("update.caddy") is not None
    assert hass.states.get("update.local") is None
    assert hass.states.get("button.pve_prune_unused_images").state != STATE_UNAVAILABLE


async def test_options_flow_has_notify_option(hass, servers) -> None:
    entry, _ = await setup_server(hass, servers)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert "notify_on_failure" in result["data_schema"].schema
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"scan_interval_minutes": 60, "refresh_apt_cache": True, "notify_on_failure": False},
    )
    await hass.async_block_till_done()
    assert entry.options["notify_on_failure"] is False


async def test_unload_cleans_up(hass, servers) -> None:
    fake = FakeServer()
    fake.reboot_required_file = True
    entry, _ = await setup_server(hass, servers, fake=fake)
    from homeassistant.helpers import issue_registry as ir

    assert ir.async_get(hass).async_get_issue(DOMAIN, f"reboot_required_{entry.entry_id}")
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"reboot_required_{entry.entry_id}") is None
