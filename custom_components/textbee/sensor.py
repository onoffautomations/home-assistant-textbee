from __future__ import annotations

from typing import Any, Tuple, List

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.components.sensor import SensorEntity

from .const import DOMAIN, DATA_COORDINATOR
from .coordinator import TextBeeCoordinator, TextBeeDeviceState


def _extract_attachments(msg: dict[str, Any]) -> Tuple[List[str], str | None]:
    """Extract attachments/media URLs from a message."""
    candidates: list[str] = []

    for key in ("media_urls", "mediaUrls", "attachments", "media", "files", "images"):
        val = msg.get(key)
        if not val:
            continue
        if isinstance(val, list):
            for item in val:
                if isinstance(item, str):
                    candidates.append(item.strip())
                elif isinstance(item, dict) and "url" in item:
                    candidates.append(str(item["url"]).strip())
        elif isinstance(val, str):
            for part in val.replace(";", ",").split(","):
                part = part.strip()
                if part:
                    candidates.append(part)
        elif isinstance(val, dict) and "url" in val:
            candidates.append(str(val["url"]).strip())

    seen = set()
    unique: list[str] = []
    for url in candidates:
        if url not in seen:
            seen.add(url)
            unique.append(url)

    primary = unique[0] if unique else None
    return unique, primary


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up TextBee sensors from a config entry."""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: TextBeeCoordinator = data[DATA_COORDINATOR]

    entities: list[SensorEntity] = []

    # Per-device sensors
    for dev_state in coordinator.data.devices.values():
        device_id = dev_state.device_id
        entities.extend(
            [
                TextBeeDeviceStatusSensor(coordinator, entry, device_id),
                TextBeeDeviceSignalSensor(coordinator, entry, device_id),
                TextBeeDeviceBatterySensor(coordinator, entry, device_id),
                TextBeeLastMessageSensor(coordinator, entry, device_id),
                TextBeeLastDirectionSensor(coordinator, entry, device_id),
                TextBeeLastIncomingNumberSensor(coordinator, entry, device_id),
                TextBeeLastOutgoingNumberSensor(coordinator, entry, device_id),
                TextBeeLastIncomingTextSensor(coordinator, entry, device_id),
                TextBeeLastOutgoingTextSensor(coordinator, entry, device_id),
                TextBeeDeviceIdSensor(coordinator, entry, device_id),
                TextBeeDeviceRegisteredSensor(coordinator, entry, device_id),
            ]
        )

    # Account-level sensors (diagnostic, no device)
    entities.extend(
        [
            TextBeeActiveDevicesSensor(coordinator, entry),
            TextBeeApiKeysSensor(coordinator, entry),
            TextBeeTotalSmsSentSensor(coordinator, entry),
            TextBeeSmsReceivedSensor(coordinator, entry),
        ]
    )

    async_add_entities(entities)


class TextBeeBaseEntity(SensorEntity):
    """Base entity with coordinator + device info."""

    _attr_should_poll = False

    def __init__(
        self,
        coordinator: TextBeeCoordinator,
        entry: ConfigEntry,
        device_id: str,
    ) -> None:
        self.coordinator = coordinator
        self.entry = entry
        self._device_id = device_id

    @property
    def device_state(self) -> TextBeeDeviceState:
        return self.coordinator.data.devices[self._device_id]

    @property
    def _effective_name(self) -> str:
        options = self.entry.options or {}
        device_names: dict[str, str] = options.get("device_names", {})
        override = device_names.get(self._device_id)
        if override:
            return override
        state = self.device_state
        return state.name or f"TextBee Device {state.device_id}"

    @property
    def device_info(self) -> DeviceInfo:
        state = self.device_state
        return DeviceInfo(
            identifiers={(DOMAIN, state.device_id)},
            name=self._effective_name,
            manufacturer="OnOff Automations",
            model=state.model or "TextBee Gateway",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


#
# PER-DEVICE SENSORS
#


class TextBeeDeviceStatusSensor(TextBeeBaseEntity):
    """Sensor for device status (Diagnostic)."""

    _attr_icon = "mdi:cellphone-wireless"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_status"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Status"

    @property
    def native_value(self) -> str | None:
        state = self.device_state
        return state.status or "unknown"


class TextBeeDeviceSignalSensor(TextBeeBaseEntity):
    """Sensor for signal bars (Diagnostic)."""

    _attr_icon = "mdi:signal-cellular-3"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_signal"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Signal Bars"

    @property
    def native_value(self) -> int | None:
        return self.device_state.signal_bars


class TextBeeDeviceBatterySensor(TextBeeBaseEntity):
    """Battery sensor for device (Diagnostic)."""

    _attr_icon = "mdi:battery"
    _attr_device_class = "battery"
    _attr_native_unit_of_measurement = "%"
    _attr_state_class = "measurement"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_battery"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Battery"

    @property
    def native_value(self) -> float | int | None:
        return self.device_state.battery_level


class TextBeeLastMessageSensor(TextBeeBaseEntity):
    """Sensor exposing last incoming/outgoing message content."""

    _attr_icon = "mdi:message-text"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_message"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Last Message"

    @property
    def native_value(self) -> str | None:
        msg = self.device_state.last_message or {}
        text = msg.get("message") or msg.get("body") or ""
        if not text:
            return None
        return text[:80] if len(text) > 80 else text

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        msg = self.device_state.last_message or {}
        attachments, primary = _extract_attachments(msg)
        sender = msg.get("sender") or msg.get("from") or msg.get("senderNumber")
        recipients = msg.get("recipients") or msg.get("recipient") or msg.get("to")
        sms_id = msg.get("_id") or msg.get("id") or msg.get("smsId")
        created_at = msg.get("createdAt")
        sent_at = msg.get("sentAt")
        received_at = msg.get("receivedAt")

        state = self.device_state

        return {
            "device_id": state.device_id,
            "device_name": self._effective_name,
            "sms_id": sms_id,
            "sender": sender,
            "recipients": recipients,
            "message": msg.get("message") or msg.get("body"),
            "status": msg.get("status"),
            "direction": state.last_direction,
            "created_at": created_at,
            "sent_at": sent_at,
            "received_at": received_at,
            "has_attachment": bool(attachments),
            "attachments": attachments,
            "primary_attachment": primary,
            "raw": msg,
        }


class TextBeeLastDirectionSensor(TextBeeBaseEntity):
    """Sensor whether last message was sent or received."""

    _attr_icon = "mdi:swap-horizontal"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_direction"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Last Message Direction"

    @property
    def native_value(self) -> str | None:
        return self.device_state.last_direction


class TextBeeLastIncomingNumberSensor(TextBeeBaseEntity):
    """Last incoming number."""

    _attr_icon = "mdi:arrow-down-bold"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_incoming_number"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Last Incoming Number"

    @property
    def native_value(self) -> str | None:
        return self.device_state.last_incoming_from


class TextBeeLastOutgoingNumberSensor(TextBeeBaseEntity):
    """Last outgoing number(s)."""

    _attr_icon = "mdi:arrow-up-bold"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_outgoing_number"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Last Outgoing Number"

    @property
    def native_value(self) -> str | None:
        return self.device_state.last_outgoing_to


class TextBeeLastIncomingTextSensor(TextBeeBaseEntity):
    """Last incoming message text."""

    _attr_icon = "mdi:message-arrow-left"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_incoming_text"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Last Incoming Message"

    @property
    def native_value(self) -> str | None:
        return self.device_state.last_incoming_text


class TextBeeLastOutgoingTextSensor(TextBeeBaseEntity):
    """Last outgoing message text."""

    _attr_icon = "mdi:message-arrow-right"

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_last_outgoing_text"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Last Outgoing Message"

    @property
    def native_value(self) -> str | None:
        return self.device_state.last_outgoing_text


#
# DIAGNOSTIC PER-DEVICE SENSORS
#


class TextBeeDeviceIdSensor(TextBeeBaseEntity):
    """Diagnostic sensor that shows the TextBee device ID with raw info."""

    _attr_icon = "mdi:identifier"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_device_id"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Device ID"

    @property
    def native_value(self) -> str | None:
        return self.device_state.device_id

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.device_state
        return {
            "device_id": state.device_id,
            "manufacturer": state.manufacturer,
            "model": state.model,
            "phone_number": state.phone_number,
            "registered": state.registered,
            "registered_at": state.registered_at,
            "raw_device": state.raw_device,
        }


class TextBeeDeviceRegisteredSensor(TextBeeBaseEntity):
    """Diagnostic sensor showing whether the device is registered."""

    _attr_icon = "mdi:check-decagram"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_registered"

    @property
    def name(self) -> str:
        return f"{self._effective_name} Registered"

    @property
    def native_value(self) -> str | None:
        state = self.device_state
        if state.registered is None:
            return None
        return "registered" if state.registered else "not_registered"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        state = self.device_state
        return {
            "registered": state.registered,
            "registered_at": state.registered_at,
        }


#
# ACCOUNT-LEVEL (STAT) SENSORS – DIAGNOSTIC, NO DEVICE
#


class TextBeeAccountBaseSensor(SensorEntity):
    """Base sensor for account-level stats (not tied to a single device)."""

    _attr_should_poll = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: TextBeeCoordinator, entry: ConfigEntry) -> None:
        self.coordinator = coordinator
        self.entry = entry

    @property
    def device_info(self) -> DeviceInfo | None:
        # No device_info => no extra "account" device in HA
        return None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.coordinator.async_add_listener(self.async_write_ha_state)
        )


class TextBeeActiveDevicesSensor(TextBeeAccountBaseSensor):
    """Number of active devices."""

    _attr_icon = "mdi:devices"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_active_devices"

    @property
    def name(self) -> str:
        return "TextBee Active Devices"

    @property
    def native_value(self) -> int:
        return len(self.coordinator.data.devices)


class TextBeeApiKeysSensor(TextBeeAccountBaseSensor):
    """Number of API keys (per entry this is 1)."""

    _attr_icon = "mdi:key-variant"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_api_keys"

    @property
    def name(self) -> str:
        return "TextBee API Keys"

    @property
    def native_value(self) -> int:
        return 1


class TextBeeTotalSmsSentSensor(TextBeeAccountBaseSensor):
    """Total SMS sent via this integration (since HA start)."""

    _attr_icon = "mdi:message-arrow-right"
    _attr_state_class = "total_increasing"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_total_sent"

    @property
    def name(self) -> str:
        return "TextBee Total SMS Sent"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.total_sent

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"scope": "since_home_assistant_start"}


class TextBeeSmsReceivedSensor(TextBeeAccountBaseSensor):
    """Total SMS received (seen by HA since start)."""

    _attr_icon = "mdi:message-arrow-left"
    _attr_state_class = "total_increasing"

    @property
    def unique_id(self) -> str:
        return f"{self.entry.entry_id}_sms_received"

    @property
    def name(self) -> str:
        return "TextBee SMS Received"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.total_received

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"scope": "since_home_assistant_start"}
