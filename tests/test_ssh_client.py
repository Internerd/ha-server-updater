"""Parsing and command logic of the SSH client, against the simulated server."""
from __future__ import annotations

from custom_components.server_updater.ssh_client import (
    ServerConnection,
    UpdateCheckResult,
    _kernel_flavor,
    _newest_kernel,
    _parse_needrestart,
    _parse_upgradable,
)

from .fake_server import FakeContainer, FakeGuest, FakeServer


def test_kernel_flavor() -> None:
    assert _kernel_flavor("6.8.12-4-pve") == "pve"
    assert _kernel_flavor("6.1.0-25-cloud-amd64") == "cloud-amd64"
    assert _kernel_flavor("6.8.0-45-generic") == "generic"
    assert _kernel_flavor("6.6.51+rpt-rpi-v8") is None


def test_newest_kernel() -> None:
    # Numeric, not lexical comparison (6.14 > 6.8, -11 > -9).
    assert _newest_kernel("6.8.12-4-pve", ["6.8.12-4-pve", "6.8.12-9-pve", "6.14.8-2-pve"]) == "6.14.8-2-pve"
    assert _newest_kernel("6.8.12-9-pve", ["6.8.12-11-pve"]) == "6.8.12-11-pve"
    # Other flavors don't count.
    assert _newest_kernel("6.1.0-25-amd64", ["6.1.0-25-amd64", "6.1.0-26-cloud-amd64"]) == "6.1.0-25-amd64"
    # Running kernel newer than anything in /boot (old ones cleaned up).
    assert _newest_kernel("6.8.12-9-pve", ["6.8.12-4-pve"]) == "6.8.12-9-pve"
    # No kernels in /boot (e.g. Raspberry Pi): nothing to compare.
    assert _newest_kernel("6.6.51+rpt-rpi-v8", []) == "6.6.51+rpt-rpi-v8"


def test_reboot_required_flags() -> None:
    assert not UpdateCheckResult(update_count=0).any_reboot_required
    assert UpdateCheckResult(update_count=0, reboot_required=True).any_reboot_required
    kernel = UpdateCheckResult(update_count=0, running_kernel="a", newest_kernel="b")
    assert kernel.kernel_reboot_required and kernel.any_reboot_required
    same = UpdateCheckResult(update_count=0, running_kernel="a", newest_kernel="a")
    assert not same.any_reboot_required


def test_parse_needrestart() -> None:
    output = (
        "NEEDRESTART-VER: 3.6\nNEEDRESTART-KCUR: 6.1.0\nNEEDRESTART-KSTA: 1\n"
        "NEEDRESTART-SVC: ssh.service\nNEEDRESTART-SVC: cron.service\nNEEDRESTART-SVC: ssh.service\n"
    )
    assert _parse_needrestart(output) == ["cron.service", "ssh.service"]
    assert _parse_needrestart("NEEDRESTART-VER: 3.6\n") == []


def test_parse_upgradable() -> None:
    packages = _parse_upgradable(
        "Listing...\nopenssl/stable-security 3.0.15-1 amd64 [upgradable from: 3.0.14-1]\n"
    )
    assert [(p.name, p.current_version, p.new_version) for p in packages] == [
        ("openssl", "3.0.14-1", "3.0.15-1")
    ]


async def _check(fake: FakeServer) -> UpdateCheckResult:
    from unittest.mock import patch

    async def _run(self, command, use_sudo=False, timeout=None):
        return fake.run(command, use_sudo=use_sudo)

    with patch.object(ServerConnection, "_run", _run):
        conn = ServerConnection("h", 22, "u")
        conn._conn = object()
        return await conn.async_check_updates()


async def test_check_debian() -> None:
    fake = FakeServer()
    fake.add_upgrade("openssl")
    result = await _check(fake)
    assert result.update_count == 1
    assert result.os_version == "12.7"
    assert not result.is_proxmox
    assert result.boot_time == fake.btime
    assert result.services_to_restart is None
    assert result.autostart_guests_stopped is None
    assert not result.any_reboot_required
    # No pvesh calls on non-Proxmox hosts.
    assert not fake.commands_matching("pvesh")


async def test_check_proxmox_kernel_and_guests() -> None:
    fake = FakeServer(proxmox=True)
    fake.kernels.append("6.8.12-9-pve")
    fake.needrestart = ["pveproxy.service"]
    fake.guests = [
        FakeGuest(100, "running-vm"),
        FakeGuest(101, "stopped-autostart", status="stopped"),
        FakeGuest(102, "stopped-manual", status="stopped", onboot=False),
        FakeGuest(103, "template", status="stopped", template=True),
        FakeGuest(200, "stopped-ct", kind="lxc", status="stopped"),
    ]
    result = await _check(fake)
    assert result.is_proxmox
    assert result.os_version == "Proxmox VE 9.0.10"
    assert result.kernel_reboot_required
    assert result.newest_kernel == "6.8.12-9-pve"
    assert result.services_to_restart == ["pveproxy.service"]
    assert [str(g) for g in result.autostart_guests_stopped] == [
        "VM 101 (stopped-autostart)",
        "CT 200 (stopped-ct)",
    ]
    # Configs are only fetched for stopped, non-template guests.
    configs = fake.commands_matching(r"/config")
    assert len(configs) == 3
    assert all(c in fake.sudo_commands for c in fake.commands_matching("^pvesh|^needrestart -b"))


async def test_check_proxmox_pvesh_failure_is_unknown() -> None:
    fake = FakeServer(proxmox=True)
    fake.failures[r"pvesh get /nodes/pve1/qemu --"] = (1, "permission denied")
    result = await _check(fake)
    assert result.autostart_guests_stopped is None


async def test_list_containers_includes_stopped_and_policy() -> None:
    fake = FakeServer()
    fake.docker = True
    fake.containers = [
        FakeContainer("web", "nginx:1", restart_policy="always"),
        FakeContainer("old", "busybox:1", state="exited", restart_policy="no"),
    ]

    from unittest.mock import patch

    async def _run(self, command, use_sudo=False, timeout=None):
        return fake.run(command, use_sudo=use_sudo)

    with patch.object(ServerConnection, "_run", _run):
        conn = ServerConnection("h", 22, "u")
        conn._conn = object()
        containers = await conn.async_list_docker_containers()
        assert [(c.name, c.state, c.restart_policy) for c in containers] == [
            ("web", "running", "always"),
            ("old", "exited", "no"),
        ]
        assert await conn.async_get_container_states() == {
            "web": ("running", "Up 5 minutes"),
            "old": ("exited", "Exited (0) 1 minute ago"),
        }
        assert await conn.async_prune_images() == "1.2GB"
        fake.docker = False
        assert await conn.async_list_docker_containers() is None
