"""A simulated Debian/Ubuntu/Proxmox VE server answering the integration's SSH commands.

Patching ServerConnection._run (instead of the high-level methods) keeps
the command building and output parsing in ssh_client.py under test. The
fake keeps state, so updates, reboots and container operations change what
later checks report.
"""
from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass, field
from types import SimpleNamespace

import asyncssh


@dataclass
class FakeContainer:
    name: str
    image_ref: str
    state: str = "running"
    restart_policy: str = "unless-stopped"
    digest: str | None = "sha256:" + "a" * 64
    labels: dict[str, str] = field(default_factory=dict)
    id: str = ""

    def __post_init__(self) -> None:
        self.id = self.id or (self.name.encode().hex() + "0" * 12)[:12]


@dataclass
class FakeGuest:
    vmid: int
    name: str
    kind: str = "qemu"
    onboot: bool = True
    status: str = "running"
    template: bool = False
    # Whether the guest actually comes up after a host reboot.
    starts_on_boot: bool = True


class FakeServer:
    def __init__(self, *, proxmox: bool = False) -> None:
        self.proxmox = proxmox
        self.os_release = (
            'PRETTY_NAME="Debian GNU/Linux 12 (bookworm)"\nID=debian\n'
            'VERSION="12 (bookworm)"\nVERSION_ID="12"\n'
        )
        self.debian_version = "12.7"
        self.pve_version = "pve-manager/9.0.10/deadbeef (running kernel: 6.8.12-4-pve)"
        self.upgradable: list[tuple[str, str, str]] = []
        self.reboot_required_file = False
        self.reboot_required_pkgs: list[str] = []
        flavor = "pve" if proxmox else "amd64"
        self.running_kernel = f"6.8.12-4-{flavor}"
        self.kernels = [self.running_kernel]
        self.btime = 1_700_000_000
        self.needrestart: list[str] | None = None
        self.guests: list[FakeGuest] = []
        self.docker = False
        self.containers: list[FakeContainer] = []
        self.compose_files: set[str] = set()
        self.reachable = True
        # SSH host key; replace it to simulate a reinstalled/other machine.
        self.host_key = new_host_key()
        # Accepted login password; None accepts any credentials.
        self.login_password: str | None = None
        # Server ignores the reboot command (e.g. blocked by a systemd inhibitor).
        self.reboot_ignored = False
        # Server doesn't come back after a reboot.
        self.stays_down_after_reboot = False
        # Packages that, once installed, request a reboot / new kernel.
        self.update_installs_kernel: str | None = None
        self.update_requests_reboot = False
        # Regex -> (exit status, stderr) for commands that should fail.
        self.failures: dict[str, tuple[int, str]] = {}
        self.commands: list[str] = []
        self.sudo_commands: list[str] = []

    # --- helpers for tests ------------------------------------------------

    def add_upgrade(self, name: str, current: str = "1.0", new: str = "1.1") -> None:
        self.upgradable.append((name, current, new))

    def commands_matching(self, pattern: str) -> list[str]:
        return [c for c in self.commands if re.search(pattern, c)]

    # --- command dispatch -------------------------------------------------

    def run(self, command: str, use_sudo: bool = False) -> SimpleNamespace:
        self.commands.append(command)
        if use_sudo:
            self.sudo_commands.append(command)
        for pattern, (status, stderr) in self.failures.items():
            if re.search(pattern, command):
                return _result(status, "", stderr)
        for pattern, handler in self._handlers():
            match = re.fullmatch(pattern, command, re.S)
            if match:
                return handler(match)
        raise AssertionError(f"Unerwarteter Befehl: {command!r}")

    def _handlers(self):
        return [
            (r"true", lambda m: _ok()),
            (r"cat /etc/os-release", lambda m: _ok(self.os_release)),
            (r"pveversion 2>/dev/null \|\| true", lambda m: _ok(self.pve_version if self.proxmox else "")),
            (r"cat /etc/debian_version 2>/dev/null \|\| true", lambda m: _ok(self.debian_version)),
            (r"apt-get update -qq.*", lambda m: _ok()),
            (r"apt list --upgradable", self._apt_list),
            (r"test -f /var/run/reboot-required && echo yes \|\| echo no",
             lambda m: _ok("yes" if self.reboot_required_file else "no")),
            (r"cat /var/run/reboot-required.pkgs 2>/dev/null \|\| true",
             lambda m: _ok("\n".join(self.reboot_required_pkgs))),
            (r"uname -r && .*", lambda m: _ok("\n".join(
                [self.running_kernel, *(f"/boot/vmlinuz-{k}" for k in self.kernels)]))),
            (r"awk '/\^btime/ \{print \$2\}' /proc/stat", lambda m: _ok(str(self.btime))),
            (r"test -x /usr/sbin/needrestart && echo yes \|\| echo no",
             lambda m: _ok("no" if self.needrestart is None else "yes")),
            (r"needrestart -b", self._needrestart),
            (r"systemctl restart (.+)", self._systemctl_restart),
            (r"hostname -s", lambda m: _ok("pve1")),
            (r"pvesh get /nodes/pve1/(qemu|lxc) --output-format json", self._pvesh_list),
            (r"pvesh get /nodes/pve1/(qemu|lxc)/(\d+)/config --output-format json", self._pvesh_config),
            (r"DEBIAN_FRONTEND=noninteractive apt-get -y .* dist-upgrade", self._dist_upgrade),
            (r"reboot", self._reboot),
            (r"docker ps -a --format '\{\{\.ID\}\}'", self._docker_ps_ids),
            (r"docker inspect (.+)", self._docker_inspect),
            (r"docker image inspect (.+)", self._docker_image_inspect),
            (r"docker ps -a --format '\{\{\.Names\}\}\t\{\{\.State\}\}\t\{\{\.Status\}\}'", self._docker_states),
            (r"docker (restart|stop|start) (.+)", self._docker_action),
            (r"docker image prune -f", lambda m: _ok("Deleted Images:\nTotal reclaimed space: 1.2GB\n")),
            (r"sh -c (.+)", self._test_files),
            (r"docker compose (.+) (pull|up -d) (\S+)", self._compose),
        ]

    # --- apt / reboot -----------------------------------------------------

    def _apt_list(self, m):
        lines = ["Listing..."] + [
            f"{name}/stable {new} amd64 [upgradable from: {current}]"
            for name, current, new in self.upgradable
        ]
        return _ok("\n".join(lines))

    def _dist_upgrade(self, m):
        self.upgradable.clear()
        if self.update_installs_kernel:
            self.kernels.append(self.update_installs_kernel)
        if self.update_requests_reboot:
            self.reboot_required_file = True
            self.reboot_required_pkgs = ["libc6"]
        return _ok("done")

    def _reboot(self, m):
        if self.reboot_ignored:
            return _ok()
        self.btime += 3600
        self.running_kernel = max(self.kernels, key=lambda k: [int(p) if p.isdigit() else p for p in re.findall(r"\d+|\D+", k)])
        self.reboot_required_file = False
        self.reboot_required_pkgs = []
        if self.needrestart is not None:
            self.needrestart = []
        for guest in self.guests:
            if guest.onboot:
                guest.status = "running" if guest.starts_on_boot else "stopped"
        if self.stays_down_after_reboot:
            self.reachable = False
        # A real reboot drops the SSH session.
        raise ConnectionResetError("connection lost")

    # --- needrestart ------------------------------------------------------

    def _needrestart(self, m):
        lines = ["NEEDRESTART-VER: 3.6", "NEEDRESTART-KSTA: 1"]
        lines += [f"NEEDRESTART-SVC: {svc}" for svc in self.needrestart or []]
        return _ok("\n".join(lines))

    def _systemctl_restart(self, m):
        services = shlex.split(m.group(1))
        self.needrestart = [s for s in self.needrestart or [] if s not in services]
        return _ok()

    # --- Proxmox VE -------------------------------------------------------

    def _pvesh_list(self, m):
        kind = m.group(1)
        return _ok(json.dumps([
            {"vmid": g.vmid if kind == "qemu" else str(g.vmid), "name": g.name,
             "status": g.status, **({"template": 1} if g.template else {})}
            for g in self.guests if g.kind == kind
        ]))

    def _pvesh_config(self, m):
        guest = next(g for g in self.guests if g.kind == m.group(1) and g.vmid == int(m.group(2)))
        return _ok(json.dumps({"name": guest.name, **({"onboot": 1} if guest.onboot else {})}))

    # --- docker -----------------------------------------------------------

    def _container(self, key: str) -> FakeContainer | None:
        return next((c for c in self.containers if key in (c.id, c.name)), None)

    def _docker_ps_ids(self, m):
        if not self.docker:
            return _result(127, "", "docker: command not found")
        return _ok("\n".join(c.id for c in self.containers))

    def _docker_inspect(self, m):
        out = []
        for key in m.group(1).split():
            c = self._container(key)
            out.append({
                "Id": c.id + "f" * 52,
                "Name": "/" + c.name,
                "Config": {"Image": c.image_ref, "Labels": c.labels},
                "State": {"Status": c.state},
                "HostConfig": {"RestartPolicy": {"Name": c.restart_policy}},
            })
        return _ok(json.dumps(out))

    def _docker_image_inspect(self, m):
        ref = m.group(1)
        c = next(c for c in self.containers if c.image_ref == ref)
        repo = ref.split(":")[0]
        return _ok(json.dumps([{
            "RepoDigests": [f"{repo}@{c.digest}"] if c.digest else [],
            "Config": {"Labels": {"org.opencontainers.image.source": f"https://example.com/{repo}"}},
        }]))

    def _docker_states(self, m):
        return _ok("\n".join(
            f"{c.name}\t{c.state}\t{'Up 5 minutes' if c.state == 'running' else 'Exited (0) 1 minute ago'}"
            for c in self.containers
        ))

    def _docker_action(self, m):
        action, name = m.group(1), shlex.split(m.group(2))[0]
        c = self._container(name)
        if c is None:
            return _result(1, "", f"Error: No such container: {name}")
        c.state = "exited" if action == "stop" else "running"
        return _ok(name)

    def _test_files(self, m):
        script = shlex.split(m.group(1))[0]
        tokens = shlex.split(script)
        assert all(t in ("test", "-f", "&&") or t.startswith("/") for t in tokens), script
        paths = [tokens[i + 1] for i, t in enumerate(tokens) if t == "-f"]
        return _ok() if all(p in self.compose_files for p in paths) else _result(1)

    def _compose(self, m):
        if m.group(2) == "up -d":
            for c in self.containers:
                if c.labels.get("com.docker.compose.service") == m.group(3):
                    c.digest = "sha256:" + "b" * 64
        return _ok()


def new_host_key() -> asyncssh.SSHKey:
    return asyncssh.generate_private_key("ssh-ed25519")


def public_key(key: asyncssh.SSHKey) -> str:
    return key.export_public_key("openssh").decode().strip()


def _ok(stdout: str = "") -> SimpleNamespace:
    return _result(0, stdout)


def _result(status: int, stdout: str = "", stderr: str = "") -> SimpleNamespace:
    return SimpleNamespace(exit_status=status, stdout=stdout, stderr=stderr)
