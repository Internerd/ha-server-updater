"""Reboot detection, reboot actions, repair flow and the post-reboot watch."""
from __future__ import annotations

from datetime import timedelta

import pytest
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    async_capture_events,
    async_fire_time_changed,
)

from custom_components.server_updater.const import DOMAIN, EVENT_SERVER_UPDATER

from .conftest import notifications, setup_server
from .fake_server import FakeGuest, FakeServer


async def _advance(hass, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass, dt_util.utcnow())
    await hass.async_block_till_done()


def _issue(hass, entry):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"reboot_required_{entry.entry_id}")


def _reboot_events(events):
    return [e.data for e in events if e.data["action"] == "reboot"]


async def test_kernel_update_on_proxmox_creates_fixable_issue(hass, servers) -> None:
    fake = FakeServer(proxmox=True)
    fake.kernels.append("6.8.12-9-pve")
    entry, _ = await setup_server(hass, servers, fake=fake)

    assert hass.states.get("binary_sensor.srv_reboot_required").state == STATE_ON
    issue = _issue(hass, entry)
    assert issue is not None and issue.is_fixable
    assert issue.data == {"entry_id": entry.entry_id}
    assert "6.8.12-9-pve" in issue.translation_placeholders["reason"]
    assert "6.8.12-4-pve" in issue.translation_placeholders["reason"]


async def test_reboot_required_file_reason_lists_packages(hass, servers) -> None:
    fake = FakeServer()
    fake.reboot_required_file = True
    fake.reboot_required_pkgs = [f"pkg{i}" for i in range(12)]
    entry, _ = await setup_server(hass, servers, fake=fake)
    reason = _issue(hass, entry).translation_placeholders["reason"]
    assert reason.startswith("Pakete: pkg0, pkg1")
    assert reason.endswith("(+2)")


async def test_repair_flow_reboots_and_watch_confirms(hass, hass_client, servers, freezer) -> None:
    fake = FakeServer(proxmox=True)
    fake.kernels.append("6.8.12-9-pve")
    entry, _ = await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    assert await async_setup_component(hass, "repairs", {})
    issue = _issue(hass, entry)
    boot_before = hass.states.get("sensor.srv_last_boot").state

    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue.issue_id}
    )
    flow = await resp.json()
    assert flow["step_id"] == "confirm"
    assert flow["description_placeholders"]["name"] == "srv"
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await resp.json())["type"] == "create_entry"

    assert fake.commands_matching(r"^reboot$")
    assert _issue(hass, entry) is None
    assert hass.states.get("binary_sensor.srv_reachable").attributes["rebooting"] is True
    # Nothing is checked right away (the server is shutting down).
    checks_before = len(fake.commands_matching("apt list"))

    await _advance(hass, freezer, 181)
    assert len(fake.commands_matching("apt list")) == checks_before + 1
    assert hass.states.get("binary_sensor.srv_reboot_required").state == STATE_OFF
    assert hass.states.get("sensor.srv_last_boot").state != boot_before
    assert _issue(hass, entry) is None
    assert _reboot_events(events) == [
        {"entry_id": entry.entry_id, "server": "srv", "action": "reboot",
         "success": True, "error": None, "container": None}
    ]
    assert hass.states.get("binary_sensor.srv_reachable").attributes["rebooting"] is False


async def test_repair_flow_aborts_on_failure(hass, hass_client, servers) -> None:
    fake = FakeServer()
    fake.reboot_required_file = True
    entry, _ = await setup_server(hass, servers, fake=fake)
    assert await async_setup_component(hass, "repairs", {})
    fake.reachable = False

    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix",
        json={"handler": DOMAIN, "issue_id": f"reboot_required_{entry.entry_id}"},
    )
    flow = await resp.json()
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    result = await resp.json()
    assert result["type"] == "abort"
    assert result["reason"] == "reboot_failed"
    assert "Zeitüberschreitung" in result["description_placeholders"]["error"]
    # The issue stays, the reboot can be retried.
    assert _issue(hass, entry) is not None


async def test_reboot_button_and_slow_boot(hass, servers, freezer) -> None:
    """Server comes back only after several retries: still reported as success."""
    fake = FakeServer()
    entry, _ = await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    fake.stays_down_after_reboot = True

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_reboot"}, blocking=True)
    await _advance(hass, freezer, 181)
    assert hass.states.get("binary_sensor.srv_reachable").state == STATE_OFF
    # The other entities go unavailable, the connectivity sensor doesn't.
    assert hass.states.get("binary_sensor.srv_reboot_required").state == "unavailable"
    await _advance(hass, freezer, 61)
    assert not _reboot_events(events)

    fake.reachable = True
    await _advance(hass, freezer, 61)
    assert hass.states.get("binary_sensor.srv_reachable").state == STATE_ON
    assert [e["success"] for e in _reboot_events(events)] == [True]


async def test_server_does_not_come_back(hass, servers, freezer) -> None:
    fake = FakeServer()
    entry, _ = await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    fake.stays_down_after_reboot = True

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_reboot"}, blocking=True)
    await _advance(hass, freezer, 181)
    for _ in range(13):
        await _advance(hass, freezer, 61)

    failures = _reboot_events(events)
    assert [e["success"] for e in failures] == [False]
    assert failures[0]["error"] == "Server nach dem Neustart nicht erreichbar"
    assert f"{DOMAIN}_{entry.entry_id}_reboot" in notifications(hass)
    # Watch stopped: no further checks are scheduled.
    count = len(fake.commands_matching("apt list"))
    await _advance(hass, freezer, 600)
    assert len(fake.commands_matching("apt list")) == count


async def test_reboot_ignored_is_detected_by_boot_time(hass, servers, freezer) -> None:
    fake = FakeServer()
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)
    fake.reboot_ignored = True

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_reboot"}, blocking=True)
    await _advance(hass, freezer, 181)
    for _ in range(13):
        await _advance(hass, freezer, 61)
    failures = _reboot_events(events)
    assert len(failures) == 1
    assert "Startzeitpunkt unverändert" in failures[0]["error"]


async def test_proxmox_watch_waits_for_autostart_guests(hass, servers, freezer) -> None:
    fake = FakeServer(proxmox=True)
    fake.guests = [FakeGuest(100, "ha"), FakeGuest(101, "dns", starts_on_boot=False)]
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_reboot"}, blocking=True)
    await _advance(hass, freezer, 181)
    assert hass.states.get("binary_sensor.srv_autostart_guests_stopped").state == STATE_ON
    assert not _reboot_events(events)

    # Guest comes up late (e.g. startup delay): success.
    fake.guests[1].status = "running"
    await _advance(hass, freezer, 61)
    assert hass.states.get("binary_sensor.srv_autostart_guests_stopped").state == STATE_OFF
    assert [e["success"] for e in _reboot_events(events)] == [True]


async def test_proxmox_guest_never_starts(hass, servers, freezer) -> None:
    fake = FakeServer(proxmox=True)
    fake.guests = [FakeGuest(101, "dns", starts_on_boot=False)]
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)

    await hass.services.async_call("button", "press", {"entity_id": "button.srv_reboot"}, blocking=True)
    await _advance(hass, freezer, 181)
    for _ in range(13):
        await _advance(hass, freezer, 61)
    failures = _reboot_events(events)
    assert len(failures) == 1
    assert failures[0]["error"] == "Gäste mit Autostart laufen nicht: VM 101 (dns)"


async def test_update_and_reboot(hass, servers, freezer) -> None:
    fake = FakeServer()
    fake.add_upgrade("linux-image-amd64")
    fake.update_installs_kernel = "6.8.12-9-amd64"
    await setup_server(hass, servers, fake=fake)
    events = async_capture_events(hass, EVENT_SERVER_UPDATER)

    await hass.services.async_call(
        "button", "press", {"entity_id": "button.srv_update_and_reboot"}, blocking=True
    )
    await hass.async_block_till_done()
    assert fake.running_kernel == "6.8.12-9-amd64"
    assert [(e.data["action"], e.data["success"]) for e in events] == [("update", True)]
    await _advance(hass, freezer, 181)
    assert [(e.data["action"], e.data["success"]) for e in events] == [
        ("update", True),
        ("reboot", True),
    ]
    assert hass.states.get("update.srv_system_packages").state == STATE_OFF


async def test_reboot_refused_while_busy(hass, servers) -> None:
    entry, _ = await setup_server(hass, servers)
    coordinator = hass.data[DOMAIN][entry.entry_id]["apt"]
    coordinator.busy = True
    coordinator.async_update_listeners()
    assert hass.states.get("button.srv_reboot").state == "unavailable"
    with pytest.raises(Exception, match="läuft bereits"):
        await coordinator.async_reboot()


async def test_reboot_button_raises_on_failure(hass, servers) -> None:
    _, fake = await setup_server(hass, servers)
    fake.reachable = False
    with pytest.raises(HomeAssistantError, match="Zeitüberschreitung"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.srv_reboot"}, blocking=True
        )


async def test_services_only_a_reboot_updates_create_fixable_issue(
    hass, hass_client, servers, freezer
) -> None:
    fake = FakeServer()
    fake.needrestart = ["dbus.service", "systemd-logind.service", "cron.service"]
    entry, _ = await setup_server(hass, servers, fake=fake)

    # cron alone could be restarted without a reboot: no issue for it.
    reboot_sensor = hass.states.get("binary_sensor.srv_reboot_required")
    assert reboot_sensor.state == STATE_ON
    assert reboot_sensor.attributes["services_requiring_reboot"] == [
        "dbus.service",
        "systemd-logind.service",
    ]
    issue = _issue(hass, entry)
    assert issue is not None and issue.is_fixable
    reason = issue.translation_placeholders["reason"]
    assert "dbus.service" in reason and "cron.service" not in reason

    assert await async_setup_component(hass, "repairs", {})
    client = await hass_client()
    resp = await client.post(
        "/api/repairs/issues/fix", json={"handler": DOMAIN, "issue_id": issue.issue_id}
    )
    flow = await resp.json()
    resp = await client.post(f"/api/repairs/issues/fix/{flow['flow_id']}", json={})
    assert (await resp.json())["type"] == "create_entry"
    assert fake.commands_matching(r"^reboot$")

    await _advance(hass, freezer, 181)
    assert hass.states.get("binary_sensor.srv_reboot_required").state == STATE_OFF
    assert _issue(hass, entry) is None


async def test_restartable_services_alone_need_no_reboot(hass, servers) -> None:
    fake = FakeServer()
    fake.needrestart = ["cron.service"]
    entry, _ = await setup_server(hass, servers, fake=fake)
    assert hass.states.get("binary_sensor.srv_reboot_required").state == STATE_OFF
    assert _issue(hass, entry) is None
