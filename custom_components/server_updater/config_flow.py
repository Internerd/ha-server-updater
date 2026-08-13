"""Config flow for the Server Updater integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
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
    CONF_PRIVATE_KEY,
    CONF_PRIVATE_KEY_PASSPHRASE,
    CONF_REFRESH_APT_CACHE,
    CONF_SCAN_INTERVAL_MINUTES,
    CONF_SUDO_PASSWORD,
    CONF_USE_SUDO,
    DEFAULT_PORT,
    DEFAULT_REFRESH_APT_CACHE,
    DEFAULT_USE_SUDO,
    DOMAIN,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .ssh_client import ServerConnection, ServerUpdaterError

_LOGGER = logging.getLogger(__name__)


class ServerUpdaterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for one server.

    Everything lives on a single form so a failed connection test can be
    corrected and resubmitted in place, instead of forcing the user through
    a multi-step wizard with no way back.
    """

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        description_placeholders: dict[str, str] = {}

        if user_input is not None:
            self._data.update(user_input)
            self._data[CONF_PORT] = int(self._data[CONF_PORT])
            errors, description_placeholders = await self._async_validate()
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
                vol.Optional(
                    CONF_PASSWORD, default=data.get(CONF_PASSWORD, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Optional(
                    CONF_PRIVATE_KEY, default=data.get(CONF_PRIVATE_KEY, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)),
                vol.Optional(
                    CONF_PRIVATE_KEY_PASSPHRASE,
                    default=data.get(CONF_PRIVATE_KEY_PASSPHRASE, ""),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
                vol.Required(
                    CONF_USE_SUDO, default=data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO)
                ): BooleanSelector(),
                vol.Optional(
                    CONF_SUDO_PASSWORD, default=data.get(CONF_SUDO_PASSWORD, "")
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD)),
            }
        )

    async def _async_validate(self) -> tuple[dict[str, str], dict[str, str]]:
        errors: dict[str, str] = {}
        placeholders: dict[str, str] = {}

        password = self._data.get(CONF_PASSWORD) or None
        private_key = self._data.get(CONF_PRIVATE_KEY) or None
        passphrase = self._data.get(CONF_PRIVATE_KEY_PASSPHRASE) or None

        if not password and not private_key:
            errors["base"] = "auth_required"
            return errors, placeholders

        unique_id = (
            f"{self._data[CONF_HOST]}:{self._data[CONF_PORT]}:{self._data[CONF_USERNAME]}"
        )
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        conn = ServerConnection(
            host=self._data[CONF_HOST],
            port=self._data[CONF_PORT],
            username=self._data[CONF_USERNAME],
            password=password,
            private_key=private_key,
            private_key_passphrase=passphrase,
            use_sudo=self._data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO),
            sudo_password=self._data.get(CONF_SUDO_PASSWORD) or None,
        )
        try:
            async with conn:
                await conn.async_test_connection()
        except ServerUpdaterError as err:
            _LOGGER.warning("Verbindungstest fehlgeschlagen: %s", err)
            errors["base"] = "cannot_connect"
            placeholders["error"] = str(err)

        return errors, placeholders

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
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
