from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Dict

import logging

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    UpdateFailed,
)
from homeassistant.helpers.event import async_call_later
from homeassistant.util import dt as dt_util

from .api import TextBeeClient, TextBeeError
from .const import DOMAIN, DEFAULT_POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass
class TextBeeDeviceState:
    """State snapshot per TextBee device."""

    device_id: str

    # Basic identity
    name: str | None = None
    phone_number: str | None = None
    manufacturer: str | None = None
    model: str | None = None

    # Network / hardware
    signal_bars: int | None = None
    signal_value: int | float | None = None
    battery_level: int | float | None = None
    registered_at: str | None = None
    registered: bool | None = None

    # Status & messages
    status: str | None = None
    last_message: dict[str, Any] | None = None
    last_message_id: str | None = None
    new_message_pulse: bool = False
    last_error: str | None = None

    # Counters (since HA start)
    sent_count: int = 0
    received_count: int = 0

    # Raw device payload from API
    raw_device: dict[str, Any] | None = None

    # Direction / numbers / texts
    last_direction: str | None = None  # "incoming" / "outgoing"
    last_incoming_from: str | None = None
    last_incoming_text: str | None = None
    last_outgoing_to: str | None = None
    last_outgoing_text: str | None = None


@dataclass
class TextBeeCoordinatorData:
    """All state managed by the coordinator."""

    devices: dict[str, TextBeeDeviceState] = field(default_factory=dict)
    total_sent: int = 0
    total_received: int = 0


class TextBeeCoordinator(DataUpdateCoordinator[TextBeeCoordinatorData]):
    """Coordinator to manage TextBee devices and states."""

    def __init__(self, hass: HomeAssistant, client: TextBeeClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} coordinator",
            update_interval=timedelta(seconds=DEFAULT_POLL_INTERVAL),
        )
        self.client = client
        self.data = TextBeeCoordinatorData()

        # Auto-reply config/state
        self._auto_reply_enabled: dict[str, bool] = {}
        self._auto_reply_message: dict[str, str] = {}
        # per device_id -> { sender_number -> last_autoreply_utc }
        self._auto_reply_last: dict[str, dict[str, dt_util.dt.datetime]] = {}

    async def _async_update_data(self) -> TextBeeCoordinatorData:
        """Fetch devices & incoming SMS for each device."""
        try:
            devices_raw = await self.client.async_get_devices()
        except TextBeeError as err:
            raise UpdateFailed(str(err)) from err

        new_devices: dict[str, TextBeeDeviceState] = dict(self.data.devices)

        for dev in devices_raw or []:
            dev_id = str(dev.get("id") or dev.get("_id") or dev.get("deviceId"))
            if not dev_id:
                continue

            state = new_devices.get(dev_id) or TextBeeDeviceState(device_id=dev_id)

            # Keep raw device payload
            state.raw_device = dev

            # Basic fields
            state.name = (
                dev.get("name")
                or dev.get("label")
                or dev.get("deviceName")
                or state.name
            )
            state.phone_number = (
                dev.get("phoneNumber")
                or dev.get("phone_number")
                or dev.get("msisdn")
                or dev.get("phone")
                or state.phone_number
            )

            # Make / model
            state.manufacturer = (
                dev.get("manufacturer")
                or dev.get("brand")
                or dev.get("oem")
                or (state.raw_device or {}).get("manufacturer")
                or state.manufacturer
            )
            state.model = (
                dev.get("model")
                or dev.get("deviceModel")
                or dev.get("device_model")
                or (state.raw_device or {}).get("model")
                or state.model
            )

            # Signal
            signal_bars = dev.get("signalBars")
            signal_val = (
                dev.get("signal_strength")
                or dev.get("signalStrength")
                or dev.get("signal")
                or dev.get("signal_level")
            )

            if isinstance(signal_bars, (int, float)):
                state.signal_bars = int(signal_bars)
            elif isinstance(signal_val, (int, float)):
                v = float(signal_val)
                if v <= 0:
                    bars = 0
                elif v < 25:
                    bars = 1
                elif v < 50:
                    bars = 2
                elif v < 75:
                    bars = 3
                else:
                    bars = 4
                state.signal_bars = bars
                state.signal_value = v

            # Battery
            battery = (
                dev.get("batteryLevel")
                or dev.get("battery")
                or dev.get("battery_percentage")
                or dev.get("batteryPct")
                or dev.get("battery_percent")
            )
            if isinstance(battery, (int, float)):
                state.battery_level = battery

            # Registered
            state.registered_at = (
                dev.get("registeredAt")
                or dev.get("createdAt")
                or dev.get("lastSeen")
                or state.registered_at
            )
            if "registered" in dev:
                state.registered = bool(dev.get("registered"))
            elif state.registered_at:
                state.registered = True

            # Status
            status = dev.get("status") or dev.get("state")
            if not status and isinstance(dev.get("online"), bool):
                status = "online" if dev["online"] else "offline"
            if not status:
                status = state.status or "online"
            state.status = status
            state.last_error = None

            # Fetch last received SMS for this device
            try:
                messages = await self.client.async_get_received_sms(dev_id)
            except TextBeeError as err:
                _LOGGER.error(
                    "Error fetching received SMS for device %s: %s", dev_id, err
                )
                state.last_error = str(err)
                new_devices[dev_id] = state
                continue

            latest_msg: dict[str, Any] | None = None
            if messages:
                def _ts(m: dict[str, Any]) -> str:
                    return (
                        m.get("receivedAt")
                        or m.get("createdAt")
                        or m.get("sentAt")
                        or ""
                    )

                messages_sorted = sorted(messages, key=_ts, reverse=True)
                latest_msg = messages_sorted[0]

            if latest_msg:
                sms_id = (
                    latest_msg.get("_id")
                    or latest_msg.get("id")
                    or latest_msg.get("smsId")
                )
                # Detect new message
                if sms_id and sms_id != state.last_message_id:
                    self._process_incoming_message(dev_id, latest_msg, sms_id)
                else:
                    state.last_message = latest_msg

            new_devices[dev_id] = state

        self.data.devices = new_devices
        return self.data

    #
    # Shared message-processing logic (used by webhook and polling)
    #

    @callback
    def _process_incoming_message(
        self, device_id: str, msg: dict[str, Any], sms_id: str | None = None
    ) -> None:
        # If the device_id isn't known (webhook using a slightly different id),
        # remap to the first known device so entities always see the pulse.
        if device_id not in self.data.devices and self.data.devices:
            _LOGGER.debug(
                "Incoming SMS for unknown device_id %s, remapping to first device",
                device_id,
            )
            device_id = next(iter(self.data.devices.keys()))

        if device_id not in self.data.devices:
            self.data.devices[device_id] = TextBeeDeviceState(device_id=device_id)

        dev_state = self.data.devices[device_id]
        dev_state.last_message = msg
        dev_state.last_message_id = (
            sms_id
            or msg.get("_id")
            or msg.get("id")
            or msg.get("smsId")
            or dev_state.last_message_id
        )

        sender = (
            msg.get("sender")
            or msg.get("from")
            or msg.get("senderNumber")
            or msg.get("phoneNumber")
        )
        text = msg.get("message") or msg.get("body")

        dev_state.last_direction = "incoming"
        dev_state.last_incoming_from = sender
        dev_state.last_incoming_text = text

        # Counters
        dev_state.received_count += 1
        self.data.total_received += 1

        # Pulse flag for 5 seconds
        dev_state.new_message_pulse = True
        dev_state.status = dev_state.status or "online"
        dev_state.last_error = None

        @callback
        def _clear_pulse(_now) -> None:
            dev_state.new_message_pulse = False
            self.async_set_updated_data(self.data)

        async_call_later(self.hass, 5.0, _clear_pulse)
        self.async_set_updated_data(self.data)

        # Auto-reply, if configured
        if sender:
            self.hass.async_create_task(
                self._async_maybe_autoreply(device_id, sender)
            )

    #
    # Webhook handling – called from __init__.py
    #

    @callback
    def handle_incoming_webhook(self, payload: dict[str, Any]) -> None:
        """Handle webhook payload from TextBee."""
        dev_id = str(
            payload.get("deviceId")
            or payload.get("device_id")
            or payload.get("device")
            or payload.get("gatewayId")
            or ""
        )
        if not dev_id:
            _LOGGER.warning("Webhook payload missing device id: %s", payload)
            return

        msg = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        sms_id = (
            msg.get("_id") if isinstance(msg, dict) else None
        ) or payload.get("smsId")

        if not isinstance(msg, dict):
            _LOGGER.warning("Unexpected webhook structure from TextBee: %s", payload)
            return

        _LOGGER.debug("Processing TextBee webhook for device %s: %s", dev_id, msg)
        self._process_incoming_message(dev_id, msg, sms_id)

    #
    # Sent counter / direction hook – called from __init__.py after a successful send
    #

    @callback
    def record_sent_sms(
        self, device_id: str, recipients: list[str] | None = None, message: str | None = None
    ) -> None:
        if device_id not in self.data.devices and self.data.devices:
            device_id = next(iter(self.data.devices.keys()))
        if device_id not in self.data.devices:
            self.data.devices[device_id] = TextBeeDeviceState(device_id=device_id)

        dev_state = self.data.devices[device_id]
        dev_state.sent_count += 1
        self.data.total_sent += 1

        dev_state.last_direction = "outgoing"
        if recipients:
            dev_state.last_outgoing_to = ", ".join(recipients)
        if message is not None:
            dev_state.last_outgoing_text = message

        self.async_set_updated_data(self.data)

    #
    # Auto-reply configuration hooks – used by switch/text entities
    #

    @callback
    def set_auto_reply_enabled(self, device_id: str, enabled: bool) -> None:
        self._auto_reply_enabled[device_id] = enabled

    @callback
    def set_auto_reply_message(self, device_id: str, message: str) -> None:
        self._auto_reply_message[device_id] = message

    async def _async_maybe_autoreply(self, device_id: str, sender: str) -> None:
        """Send auto-reply if enabled and > 1 hour since last auto-reply to this sender."""
        if not self._auto_reply_enabled.get(device_id):
            return

        message = self._auto_reply_message.get(device_id) or ""
        if not message.strip():
            return

        now = dt_util.utcnow()
        per_device: Dict[str, dt_util.dt.datetime] = self._auto_reply_last.setdefault(
            device_id, {}
        )
        last = per_device.get(sender)
        if last and (now - last) < timedelta(hours=1):
            return

        try:
            await self.client.async_send_sms(device_id, [sender], message)
        except TextBeeError as err:
            _LOGGER.error(
                "TextBee auto-reply failed for device %s to %s: %s",
                device_id,
                sender,
                err,
            )
            return

        per_device[sender] = now
        self.record_sent_sms(device_id, [sender], message)
