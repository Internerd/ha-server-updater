"""Config flow for the Server Updater integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from collections.abc import Mapping

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_ACCEPT_HOST_KEY,
    CONF_HOST_KEY,
    CONF_NOTIFY_ON_FAILURE,
    CONF_PRIVATE_KEY,
    CONF_PRIVATE_KEY_PASSPHRASE,
    CONF_REFRESH_APT_CACHE,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_SUDO_PASSWORD,
    CONF_USE_SUDO,
    DEFAULT_NOTIFY_ON_FAILURE,
    DEFAULT_PORT,
    DEFAULT_REFRESH_APT_CACHE,
    DEFAULT_USE_SUDO,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .ssh_client import (
    ServerAuthError,
    ServerConnection,
    ServerHostKeyError,
    ServerUpdaterError,
    async_fetch_host_key,
    host_key_fingerprint,
)

# Secrets are never sent back to the form; leaving them empty when
# reconfiguring keeps the stored value.
SECRET_KEYS = (
    CONF_PASSWORD,
    CONF_PRIVATE_KEY,
    CONF_PRIVATE_KEY_PASSPHRASE,
    CONF_SUDO_PASSWORD,
)


_LOGGER = logging.getLogger(__name__)


class ServerUpdaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for one server.

    Everything lives on a single form so a failed connection test can be
    corrected and resubmitted in place, instead of forcing the user through
    a multi-step wizard with no way back.

    The server's SSH host key is pinned on first contact. When reconfiguring
    or reauthenticating, a changed host key is never accepted silently: its
    fingerprint is shown (fetched without sending any credentials) and must
    be confirmed explicitly.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        # Host key the server presented that the user was asked to confirm.
        self._offered_host_key: str | None = None

    # --- new server ------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            self._data[CONF_PORT] = int(self._data[CONF_PORT])
            if not self._has_auth(self._data):
                errors["base"] = "auth_required"
            else:
                await self.async_set_unique_id(self._unique_id(self._data))
                self._abort_if_unique_id_configured()
                errors, description_placeholders = await self._async_validate(
                    self._data, pinned_host_key=None, accept_new=False
                )
            if not errors:
                return self.async_create_entry(title=self._data[CONF_NAME], data=self._data)

        return self.async_show_form(
            step_id="user",
            data_schema=self._schema(),
            errors=errors,
            description_placeholders=description_placeholders,
        )

    def _schema(self) -> vol.Schema:
        data = self._data
        return vol.Schema(
            {
                **self._connection_fields(data),
                **self._credential_fields(data),
            }
        )

    # --- reconfigure (address, credentials, sudo) ------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {"name": entry.title}

        if user_input is not None:
            data = self._merge(entry.data, user_input)
            data[CONF_PORT] = int(data[CONF_PORT])
            self._data = data
            unique_id = self._unique_id(data)
            if not self._has_auth(data):
                errors["base"] = "auth_required"
            elif self._unique_id_taken(unique_id, entry):
                errors["base"] = "already_configured"
            else:
                errors, extra = await self._async_validate(
                    data,
                    pinned_host_key=entry.data.get(CONF_HOST_KEY),
                    accept_new=bool(user_input.get(CONF_ACCEPT_HOST_KEY)),
                )
                placeholders.update(extra)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    title=data[CONF_NAME],
                    data=data,
                    reason="reconfigure_successful",
                )
        else:
            self._data = {**entry.data, CONF_NAME: entry.title}

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    **self._connection_fields(self._data),
                    **self._credential_fields(self._data, keep_secrets=True),
                    **self._accept_field(),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    # --- reauth (login rejected or host key changed) ---------------------

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {
            "name": entry.title,
            "host": f"{entry.data[CONF_HOST]}:{entry.data.get(CONF_PORT, DEFAULT_PORT)}",
        }

        if user_input is not None:
            data = self._merge(entry.data, user_input)
            self._data = data
            unique_id = self._unique_id(data)
            if not self._has_auth(data):
                errors["base"] = "auth_required"
            elif self._unique_id_taken(unique_id, entry):
                errors["base"] = "already_configured"
            else:
                errors, extra = await self._async_validate(
                    data,
                    pinned_host_key=entry.data.get(CONF_HOST_KEY),
                    accept_new=bool(user_input.get(CONF_ACCEPT_HOST_KEY)),
                )
                placeholders.update(extra)
            if not errors:
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data=data,
                    reason="reauth_successful",
                )
        else:
            self._data = dict(entry.data)

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_USERNAME, default=self._data.get(CONF_USERNAME, "")
                    ): TextSelector(),
                    **self._credential_fields(self._data, keep_secrets=True),
                    **self._accept_field(),
                }
            ),
            errors=errors,
            description_placeholders=placeholders,
        )

    # --- helpers -----------------------------------------------------------

    @staticmethod
    def _unique_id(data: Mapping[str, Any]) -> str:
        return f"{data[CONF_HOST]}:{data[CONF_PORT]}:{data[CONF_USERNAME]}"

    def _unique_id_taken(self, unique_id: str, entry: ConfigEntry) -> bool:
        return any(
            other.unique_id == unique_id and other.entry_id != entry.entry_id
            for other in self._async_current_entries(include_ignore=False)
        )

    @staticmethod
    def _has_auth(data: Mapping[str, Any]) -> bool:
        return bool(data.get(CONF_PASSWORD) or data.get(CONF_PRIVATE_KEY))

    @staticmethod
    def _merge(stored: Mapping[str, Any], user_input: Mapping[str, Any]) -> dict[str, Any]:
        """Apply form input to stored data; empty secret fields keep the stored value."""
        data = dict(stored)
        for key, value in user_input.items():
            if key == CONF_ACCEPT_HOST_KEY:
                continue
            if key in SECRET_KEYS and not value:
                continue
            data[key] = value
        return data

    @staticmethod
    def _connection_fields(data: Mapping[str, Any]) -> dict:
        return {
            vol.Required(CONF_NAME, default=data.get(CONF_NAME, "")): TextSelector(),
            vol.Required(CONF_HOST, default=data.get(CONF_HOST, "")): TextSelector(),
            vol.Required(
                CONF_PORT, default=data.get(CONF_PORT, DEFAULT_PORT)
            ): vol.All(
                NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                ),
                vol.Coerce(int),
            ),
            vol.Required(
                CONF_USERNAME, default=data.get(CONF_USERNAME, "")
            ): TextSelector(),
        }

    @staticmethod
    def _credential_fields(data: Mapping[str, Any], keep_secrets: bool = False) -> dict:
        """Credential fields; with keep_secrets, they start empty (= unchanged)."""

        def secret_default(key: str) -> str:
            return "" if keep_secrets else data.get(key, "")

        password = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))
        return {
            vol.Optional(CONF_PASSWORD, default=secret_default(CONF_PASSWORD)): password,
            vol.Optional(
                CONF_PRIVATE_KEY, default=secret_default(CONF_PRIVATE_KEY)
            ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)),
            vol.Optional(
                CONF_PRIVATE_KEY_PASSPHRASE,
                default=secret_default(CONF_PRIVATE_KEY_PASSPHRASE),
            ): password,
            vol.Required(
                CONF_USE_SUDO, default=data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO)
            ): BooleanSelector(),
            vol.Optional(
                CONF_SUDO_PASSWORD, default=secret_default(CONF_SUDO_PASSWORD)
            ): password,
        }

    def _accept_field(self) -> dict:
        """Confirmation checkbox, only once a changed host key was shown."""
        if self._offered_host_key is None:
            return {}
        return {vol.Optional(CONF_ACCEPT_HOST_KEY, default=False): BooleanSelector()}

    async def _async_validate(
        self,
        data: dict[str, Any],
        pinned_host_key: str | None,
        accept_new: bool,
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Test the connection; on success the host key to pin is put into data.

        pinned_host_key None means first contact: any host key is accepted.
        Otherwise a different host key is only used if the user confirmed
        exactly the key whose fingerprint was shown to them before.
        """
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        host_key = pinned_host_key
        if accept_new and self._offered_host_key:
            host_key = self._offered_host_key

        conn = self._build_connection(data, host_key)
        try:
            async with conn:
                await conn.async_test_connection()
        except ServerHostKeyError as err:
            try:
                offered = await async_fetch_host_key(data[CONF_HOST], data[CONF_PORT])
                fingerprint = host_key_fingerprint(offered)
            except (ServerUpdaterError, ValueError) as fetch_err:
                _LOGGER.warning("Hostschlüssel nicht abrufbar: %s", fetch_err)
                errors["base"] = "cannot_connect"
                placeholders["error"] = str(err)
                return errors, placeholders
            self._offered_host_key = offered
            errors["base"] = "host_key_changed"
            placeholders["fingerprint"] = fingerprint
            return errors, placeholders
        except ServerAuthError as err:
            _LOGGER.warning("Anmeldung fehlgeschlagen: %s", err)
            errors["base"] = "invalid_auth"
            placeholders["error"] = str(err)
            return errors, placeholders
        except ServerUpdaterError as err:
            _LOGGER.warning("Verbindungstest fehlgeschlagen: %s", err)
            errors["base"] = "cannot_connect"
            placeholders["error"] = str(err)
            return errors, placeholders

        if conn.server_host_key:
            data[CONF_HOST_KEY] = conn.server_host_key
        return errors, placeholders

    @staticmethod
    def _build_connection(data: Mapping[str, Any], host_key: str | None) -> ServerConnection:
        return ServerConnection(
            host=data[CONF_HOST],
            port=data[CONF_PORT],
            username=data[CONF_USERNAME],
            password=data.get(CONF_PASSWORD) or None,
            private_key=data.get(CONF_PRIVATE_KEY) or None,
            private_key_passphrase=data.get(CONF_PRIVATE_KEY_PASSPHRASE) or None,
            use_sudo=data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO),
            sudo_password=data.get(CONF_SUDO_PASSWORD) or None,
            host_key=host_key,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return ServerUpdaterOptionsFlow()


class ServerUpdaterOptionsFlow(OptionsFlow):
    """Handle options for an existing server entry."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES,
                    default=current.get(CONF_SCAN_INTERVAL_MINUTES, 360),
                ): vol.All(
                    NumberSelector(
                        NumberSelectorConfig(
                            min=MIN_SCAN_INTERVAL_MINUTES,
                            max=1440,
                            mode=NumberSelectorMode.BOX,
                        )
                    ),
                    vol.Coerce(int),
                ),
                vol.Required(
                    CONF_REFRESH_APT_CACHE,
                    default=current.get(
                        CONF_REFRESH_APT_CACHE, DEFAULT_REFRESH_APT_CACHE
                    ),
                ): BooleanSelector(),
                vol.Required(
                    CONF_NOTIFY_ON_FAILURE,
                    default=current.get(
                        CONF_NOTIFY_ON_FAILURE, DEFAULT_NOTIFY_ON_FAILURE
                    ),
                ): BooleanSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
