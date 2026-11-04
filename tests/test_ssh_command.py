"""How commands are wrapped in sudo and environment assignments."""
from __future__ import annotations

from custom_components.server_updater.ssh_client import _is_env_rejection, build_command

ENV = {"DEBIAN_FRONTEND": "noninteractive"}


def test_plain_and_sudo() -> None:
    assert build_command("true", use_sudo=False) == ("true", None)
    assert build_command("true", use_sudo=True) == ("sudo -n -- true", None)
    assert build_command("true", use_sudo=True, sudo_password="pw") == (
        "sudo -S -p '' -- true",
        "pw\n",
    )


def test_env_never_follows_double_dash() -> None:
    # sudo-rs runs everything after "--" as the command.
    cmd, _ = build_command("apt-get -y dist-upgrade", use_sudo=True, env=ENV)
    assert cmd == "sudo -n DEBIAN_FRONTEND=noninteractive apt-get -y dist-upgrade"
    cmd, stdin = build_command("apt-get", use_sudo=True, sudo_password="pw", env=ENV)
    assert cmd == "sudo -S -p '' DEBIAN_FRONTEND=noninteractive apt-get"
    assert stdin == "pw\n"
    assert build_command("apt-get", use_sudo=False, env=ENV) == (
        "DEBIAN_FRONTEND=noninteractive apt-get",
        None,
    )
    assert build_command("x", use_sudo=False, env={"A": "a b"})[0] == "A='a b' x"


def test_env_rejection_detection() -> None:
    assert _is_env_rejection("sudo: DEBIAN_FRONTEND=noninteractive: command not found")
    assert _is_env_rejection(
        "sudo: sorry, you are not allowed to set the following environment "
        "variables: DEBIAN_FRONTEND"
    )
    assert not _is_env_rejection("E: Unable to locate package foo")
