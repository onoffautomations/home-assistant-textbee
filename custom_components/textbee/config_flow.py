from __future__ import annotations

from typing import Any

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.components.webhook import async_generate_id

from .const import (
    DOMAIN,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_WEBHOOK_ID,
    DEFAULT_BASE_URL,
)
from .api import TextBeeClient, TextBeeAuthError, TextBeeError

_LOGGER = logging.getLogger(__name__)


async def _async_validate(
    hass: HomeAssistant, api_key: str, base_url: str
) -> None:
    """Validate API key by pinging TextBee."""
    session = async_get_clientsession(hass)
    client = TextBeeClient(session, api_key=api_key, base_url=base_url)
    await client.async_ping()


class TextBeeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TextBee."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = user_input[CONF_BASE_URL].strip().rstrip("/")
            name = user_input.get(CONF_NAME) or "TextBee"

            # Unique per API key
            await self.async_set_unique_id(api_key)
            self._abort_if_unique_id_configured()

            try:
                await _async_validate(self.hass, api_key, base_url)
            except TextBeeAuthError:
                errors["base"] = "invalid_auth"
            except TextBeeError:
                _LOGGER.exception("Error communicating with TextBee")
                errors["base"] = "cannot_connect"
            else:
                webhook_id = async_generate_id()

                data = {
                    CONF_NAME: name,
                    CONF_API_KEY: api_key,
                    CONF_BASE_URL: base_url,
                    CONF_WEBHOOK_ID: webhook_id,
                }

                # Options will hold friendly names + default device
                return self.async_create_entry(title=name, data=data, options={})

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Optional(CONF_NAME, default="TextBee"): str,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return TextBeeOptionsFlowHandler(config_entry)


class TextBeeOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle TextBee options (friendly names, default device)."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        # IMPORTANT: store in a normal attribute, not touching property `config_entry`
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Manage options."""
        hass = self.hass
        entry = self._config_entry

        api_key: str = entry.data[CONF_API_KEY]
        base_url: str = entry.data[CONF_BASE_URL]

        session = async_get_clientsession(hass)
        client = TextBeeClient(session, api_key=api_key, base_url=base_url)

        try:
            devices = await client.async_get_devices()
        except TextBeeError as err:
            _LOGGER.error("Cannot fetch devices in options flow: %s", err)
            devices = []

        device_map: dict[str, str] = {}
        for dev in devices or []:
            dev_id = str(dev.get("id") or dev.get("_id") or dev.get("deviceId"))
            if not dev_id:
                continue
            label = dev.get("name") or dev.get("label") or dev.get("deviceName") or dev_id
            device_map[dev_id] = label

        current_options = dict(entry.options or {})
        device_names: dict[str, str] = current_options.get("device_names", {})
        default_device_id: str | None = current_options.get("default_device_id")

        if user_input is not None:
            new_device_names: dict[str, str] = {}
            for dev_id in device_map.keys():
                key = f"friendly_{dev_id}"
                val = user_input.get(key)
                if val:
                    new_device_names[dev_id] = val

            new_default = user_input.get("default_device_id") or default_device_id

            new_options = {
                "device_names": new_device_names,
                "default_device_id": new_default,
            }

            return self.async_create_entry(title="", data=new_options)

        if not default_device_id and device_map:
            default_device_id = next(iter(device_map.keys()))

        fields: dict[Any, Any] = {}

        if device_map:
            fields[vol.Optional(
                "default_device_id",
                default=default_device_id,
            )] = vol.In(device_map)

            for dev_id, label in device_map.items():
                default_name = device_names.get(dev_id, label)
                fields[vol.Optional(f"friendly_{dev_id}", default=default_name)] = str
        else:
            fields[vol.Optional(
                "note",
                default="No devices found for this API key yet.",
            )] = str

        schema = vol.Schema(fields)

        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors={},
        )
