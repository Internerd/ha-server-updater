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
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    AUTH_METHOD_KEY,
    AUTH_METHOD_PASSWORD,
    CONF_AUTH_METHOD,
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
    """Handle a config flow for one server."""

    VERSION = 1

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data.update(user_input)
            if user_input[CONF_AUTH_METHOD] == AUTH_METHOD_PASSWORD:
                return await self.async_step_password()
            return await self.async_step_key()

        schema = vol.Schema(
            {
                vol.Required(CONF_NAME): TextSelector(),
                vol.Required(CONF_HOST): TextSelector(),
                vol.Required(CONF_PORT, default=DEFAULT_PORT): NumberSelector(
                    NumberSelectorConfig(min=1, max=65535, mode=NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_USERNAME): TextSelector(),
                vol.Required(
                    CONF_AUTH_METHOD, default=AUTH_METHOD_PASSWORD
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[AUTH_METHOD_PASSWORD, AUTH_METHOD_KEY],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_AUTH_METHOD,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_password(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_sudo()

        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="password", data_schema=schema)

    async def async_step_key(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_sudo()

        schema = vol.Schema(
            {
                vol.Required(CONF_PRIVATE_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.TEXT, multiline=True)
                ),
                vol.Optional(CONF_PRIVATE_KEY_PASSPHRASE): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(step_id="key", data_schema=schema)

    async def async_step_sudo(
        self, user_input: dict[str, Any] | None = None
    ) -> Any:
        if user_input is not None:
            self._data.update(user_input)
            errors = await self._async_validate()
            if not errors:
                return self.async_create_entry(
                    title=self._data[CONF_NAME], data=self._data
                )
            return self.async_show_form(
                step_id="sudo", data_schema=self._sudo_schema(), errors=errors
            )

        return self.async_show_form(step_id="sudo", data_schema=self._sudo_schema())

    def _sudo_schema(self) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required(
                    CONF_USE_SUDO,
                    default=self._data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO),
                ): BooleanSelector(),
                vol.Optional(CONF_SUDO_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )

    async def _async_validate(self) -> dict[str, str]:
        errors: dict[str, str] = {}
        unique_id = (
            f"{self._data[CONF_HOST]}:{self._data[CONF_PORT]}:{self._data[CONF_USERNAME]}"
        )
        await self.async_set_unique_id(unique_id)
        self._abort_if_unique_id_configured()

        conn = ServerConnection(
            host=self._data[CONF_HOST],
            port=self._data[CONF_PORT],
            username=self._data[CONF_USERNAME],
            password=self._data.get(CONF_PASSWORD),
            private_key=self._data.get(CONF_PRIVATE_KEY),
            private_key_passphrase=self._data.get(CONF_PRIVATE_KEY_PASSPHRASE),
            use_sudo=self._data.get(CONF_USE_SUDO, DEFAULT_USE_SUDO),
            sudo_password=self._data.get(CONF_SUDO_PASSWORD),
        )
        try:
            await conn.async_test_connection()
        except ServerUpdaterError as err:
            _LOGGER.warning("Verbindungstest fehlgeschlagen: %s", err)
            errors["base"] = "cannot_connect"
        finally:
            await conn.close()

        return errors

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
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=1440,
                        mode=NumberSelectorMode.BOX,
                    )
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
