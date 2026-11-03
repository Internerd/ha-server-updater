"""Config flow: adding a server, reconfiguring it, reauthentication and host key pinning."""
from __future__ import annotations

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.server_updater.const import CONF_HOST_KEY, DOMAIN
from custom_components.server_updater.ssh_client import host_key_fingerprint

from .conftest import setup_server
from .fake_server import FakeServer, new_host_key, public_key

USER_INPUT = {
    "name": "Web",
    "host": "web",
    "port": 22,
    "username": "ha",
    "password": "secret",
    "private_key": "",
    "private_key_passphrase": "",
    "use_sudo": True,
    "sudo_password": "",
}


def reconfigure_input(**changes) -> dict:
    data = {
        "name": "srv",
        "host": "srv",
        "port": 22,
        "username": "ha",
        "password": "",
        "private_key": "",
        "private_key_passphrase": "",
        "use_sudo": True,
        "sudo_password": "",
    }
    data.update(changes)
    return data


async def test_user_flow_pins_host_key(hass, servers) -> None:
    fake = servers["web"] = FakeServer()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST_KEY] == public_key(fake.host_key)
    assert result["result"].unique_id == "web:22:ha"


async def test_user_flow_invalid_auth(hass, servers) -> None:
    fake = servers["web"] = FakeServer()
    fake.login_password = "other"
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_existing_entry_gets_host_key_pinned_without_reload(hass, servers) -> None:
    entry, fake = await setup_server(hass, servers)
    assert entry.data[CONF_HOST_KEY] == public_key(fake.host_key)
    assert entry.state is ConfigEntryState.LOADED
    # Pinning must not have triggered a reload (only option changes do).
    assert len(fake.commands_matching(r"^cat /etc/os-release$")) == 1


async def test_changed_host_key_starts_reauth_and_requires_confirmation(hass, servers) -> None:
    entry, fake = await setup_server(hass, servers)
    old_key = entry.data[CONF_HOST_KEY]
    fake.host_key = new_host_key()
    fake.login_password = "secret"

    coordinator = hass.data[DOMAIN][entry.entry_id]["apt"]
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [f["context"]["source"] for f in flows] == [SOURCE_REAUTH]
    flow_id = flows[0]["flow_id"]
    commands_before = len(fake.commands)

    # Credentials are not sent to a server with an unconfirmed host key.
    result = await hass.config_entries.flow.async_configure(
        flow_id, {"username": "ha", "password": "secret", "use_sudo": True}
    )
    assert result["errors"] == {"base": "host_key_changed"}
    expected = host_key_fingerprint(public_key(fake.host_key))
    assert result["description_placeholders"]["fingerprint"] == expected
    assert "accept_new_host_key" in result["data_schema"].schema
    assert len(fake.commands) == commands_before
    assert entry.data[CONF_HOST_KEY] == old_key

    result = await hass.config_entries.flow.async_configure(
        flow_id,
        {"username": "ha", "password": "secret", "use_sudo": True, "accept_new_host_key": True},
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_HOST_KEY] == public_key(fake.host_key)
    assert entry.data["password"] == "secret"
    assert entry.state is ConfigEntryState.LOADED


async def test_rejected_login_starts_reauth(hass, servers) -> None:
    entry, fake = await setup_server(hass, servers)
    fake.login_password = "new"
    await hass.data[DOMAIN][entry.entry_id]["apt"].async_refresh()
    await hass.async_block_till_done()

    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert len(flows) == 1
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {"username": "ha", "password": "wrong", "use_sudo": True}
    )
    assert result["errors"] == {"base": "invalid_auth"}
    result = await hass.config_entries.flow.async_configure(
        flows[0]["flow_id"], {"username": "ha", "password": "new", "use_sudo": True}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data["password"] == "new"


async def test_reconfigure_new_ip_same_server(hass, servers) -> None:
    entry, fake = await setup_server(hass, servers)
    hass.config_entries.async_update_entry(
        entry, data={**entry.data, "password": "keep-me", "sudo_password": "sudo-pw"}
    )
    servers["10.0.0.5"] = fake

    result = await entry.start_reconfigure_flow(hass)
    assert result["step_id"] == "reconfigure"
    # Secrets are never sent back to the form.
    defaults = {
        str(key): key.default() for key in result["data_schema"].schema if callable(key.default)
    }
    assert defaults["password"] == "" and defaults["sudo_password"] == ""
    assert defaults["host"] == "srv"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(host="10.0.0.5", port=2222, name="Neu")
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "10.0.0.5"
    assert entry.data["port"] == 2222
    assert entry.data["password"] == "keep-me"
    assert entry.data["sudo_password"] == "sudo-pw"
    assert entry.unique_id == "10.0.0.5:2222:ha"
    assert entry.title == "Neu"
    assert entry.state is ConfigEntryState.LOADED


async def test_reconfigure_other_machine_requires_confirmation(hass, servers) -> None:
    entry, _ = await setup_server(hass, servers)
    other = servers["new-host"] = FakeServer()

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(password="pw", host="new-host")
    )
    assert result["errors"] == {"base": "host_key_changed"}
    assert other.commands == []
    assert entry.data["host"] == "srv"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(password="pw", host="new-host", accept_new_host_key=True)
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["host"] == "new-host"
    assert entry.data[CONF_HOST_KEY] == public_key(other.host_key)


async def test_reconfigure_rejects_key_swapped_after_confirmation(hass, servers) -> None:
    entry, _ = await setup_server(hass, servers)
    other = servers["new-host"] = FakeServer()
    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(password="pw", host="new-host")
    )
    # The key changes again between showing and confirming the fingerprint.
    other.host_key = new_host_key()
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(password="pw", host="new-host", accept_new_host_key=True)
    )
    assert result["errors"] == {"base": "host_key_changed"}
    assert result["description_placeholders"]["fingerprint"] == host_key_fingerprint(
        public_key(other.host_key)
    )
    assert entry.data["host"] == "srv"


async def test_reconfigure_duplicate_and_unreachable(hass, servers) -> None:
    entry, _ = await setup_server(hass, servers)
    MockConfigEntry(domain=DOMAIN, unique_id="other:22:ha", data={}).add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(password="pw", host="other")
    )
    assert result["errors"] == {"base": "already_configured"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], reconfigure_input(password="pw", host="nowhere")
    )
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data["host"] == "srv"
