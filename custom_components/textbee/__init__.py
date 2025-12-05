from __future__ import annotations

from typing import Any

import logging
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import device_registry as dr
from homeassistant.components import webhook

from .const import (
    DOMAIN,
    PLATFORMS,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_WEBHOOK_ID,
    DATA_CLIENT,
    DATA_COORDINATOR,
)
from .api import TextBeeClient, TextBeeError
from .coordinator import TextBeeCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_SEND_MESSAGE = "send_message"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up TextBee (YAML not used, config flow only)."""
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up TextBee from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    api_key: str = entry.data[CONF_API_KEY]
    base_url: str = entry.data[CONF_BASE_URL]
    webhook_id: str = entry.data[CONF_WEBHOOK_ID]

    session = async_get_clientsession(hass)
    client = TextBeeClient(session, api_key=api_key, base_url=base_url)

    coordinator = TextBeeCoordinator(hass, client=client)

    # Initial refresh to get devices & last messages
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        DATA_CLIENT: client,
        DATA_COORDINATOR: coordinator,
    }

    #
    # Webhook for incoming messages (optional, but supported)
    #

    @callback
    async def _handle_webhook(
        hass_: HomeAssistant,
        webhook_id_: str,
        request,
    ) -> None:
        """Handle incoming TextBee webhook."""
        try:
            payload = await request.json()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Invalid JSON in TextBee webhook: %s", err)
            return

        _LOGGER.debug("TextBee webhook payload: %s", payload)
        coordinator.handle_incoming_webhook(payload)

        # Fire HA event for advanced automations
        hass_.bus.async_fire("textbee_webhook", payload)

    webhook.async_register(
        hass,
        DOMAIN,
        "TextBee Webhook",
        webhook_id,
        _handle_webhook,
    )

    _LOGGER.info(
        "TextBee webhook registered. Configure TextBee to POST to: /api/webhook/%s",
        webhook_id,
    )

    #
    # Services
    #

    def _normalize_to_list(value: Any) -> list[str]:
        """Handle string or list, allow comma/semicolon separated values."""
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        s = str(value).strip()
        if not s:
            return []
        parts = s.replace(";", ",").split(",")
        return [p.strip() for p in parts if p.strip()]

    def _resolve_device_id_from_ha_device(
        hass_: HomeAssistant, ha_device_id: str | None
    ) -> str | None:
        if not ha_device_id:
            return None
        dev_reg = dr.async_get(hass_)
        dev = dev_reg.async_get(ha_device_id)
        if not dev or not dev.identifiers:
            return None
        for ident_domain, ident in dev.identifiers:
            if ident_domain == DOMAIN:
                return ident
        return None

    async def _async_handle_send_message(call: ServiceCall) -> None:
        data = call.data

        if not hass.data.get(DOMAIN):
            _LOGGER.error("TextBee send_message: no integration data")
            return

        # Use the first entry (one account = one entry typical)
        entry_id, entry_data = next(iter(hass.data[DOMAIN].items()))
        client: TextBeeClient = entry_data[DATA_CLIENT]
        coordinator: TextBeeCoordinator = entry_data[DATA_COORDINATOR]

        device_id: str | None = data.get("device_id")
        ha_device_id: str | None = data.get("device")

        if not device_id:
            device_id = _resolve_device_id_from_ha_device(hass, ha_device_id)

        if not device_id:
            # Use default device from options if available
            opts = hass.config_entries.async_get_entry(entry_id).options or {}
            device_id = opts.get("default_device_id")

        if not device_id and coordinator.data.devices:
            # Fallback: first known device
            device_id = next(iter(coordinator.data.devices.keys()))

        if not device_id:
            _LOGGER.error("TextBee send_message: no device_id resolved.")
            return

        recipients_raw: Any = data["recipients"]
        message: str = data.get("message", "") or ""
        media_raw: Any = data.get("media_urls", "") or ""

        recipients = _normalize_to_list(recipients_raw)
        media_urls = _normalize_to_list(media_raw)

        if not recipients:
            _LOGGER.error("TextBee send_message: no valid recipients provided.")
            return

        try:
            if media_urls:
                _LOGGER.debug(
                    "TextBee send_message: sending MMS via device %s to %s with media %s",
                    device_id,
                    recipients,
                    media_urls,
                )
                await client.async_send_mms(
                    device_id=device_id,
                    recipients=recipients,
                    message=message,
                    media_urls=media_urls,
                )
            else:
                _LOGGER.debug(
                    "TextBee send_message: sending SMS via device %s to %s",
                    device_id,
                    recipients,
                )
                await client.async_send_sms(
                    device_id=device_id,
                    recipients=recipients,
                    message=message,
                )
        except TextBeeError as err:
            _LOGGER.error("TextBee send_message failed: %s", err)
            return

        # Only record as "sent" if we didn't raise an error
        coordinator.record_sent_sms(device_id, recipients, message)

    # Register send_message service
    hass.services.async_register(
        DOMAIN,
        SERVICE_SEND_MESSAGE,
        _async_handle_send_message,
        schema=vol.Schema(
            {
                vol.Optional("device_id"): str,
                vol.Optional("device"): str,
                vol.Required("recipients"): vol.Any(str, [str]),
                vol.Required("message"): str,
                vol.Optional("media_urls", default=""): vol.Any(str, [str]),
            }
        ),
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    data = hass.data[DOMAIN].pop(entry.entry_id, None)
    if data:
        webhook_id = entry.data.get(CONF_WEBHOOK_ID)
        if webhook_id:
            webhook.async_unregister(hass, webhook_id)

    if unload_ok and not hass.data.get(DOMAIN):
        # Last entry removed – clean up services
        if hass.services.has_service(DOMAIN, SERVICE_SEND_MESSAGE):
            hass.services.async_remove(DOMAIN, SERVICE_SEND_MESSAGE)

    return unload_ok
