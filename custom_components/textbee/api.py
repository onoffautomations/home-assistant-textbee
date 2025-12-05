from __future__ import annotations

from typing import Any

import asyncio
import logging

from aiohttp import ClientError, ClientSession

_LOGGER = logging.getLogger(__name__)


class TextBeeError(Exception):
    """Base TextBee exception."""


class TextBeeAuthError(TextBeeError):
    """Authentication error."""


class TextBeeClient:
    """Async client for TextBee REST API, multi-device aware."""

    def __init__(self, session: ClientSession, api_key: str, base_url: str) -> None:
        self._session = session
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def api_key(self) -> str:
        return self._api_key

    def _headers(self) -> dict[str, str]:
        return {
            "x-api-key": self._api_key,
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        _LOGGER.debug("TextBee request %s %s", method, url)

        try:
            async with self._session.request(
                method, url, headers=self._headers(), json=json, timeout=15
            ) as resp:
                if resp.status == 401:
                    raise TextBeeAuthError("Invalid API key")
                text = await resp.text()
                if resp.status >= 400:
                    _LOGGER.error(
                        "TextBee API error %s %s: %s", resp.status, url, text
                    )
                    raise TextBeeError(f"API error {resp.status}: {text}")

                if not text:
                    return None

                try:
                    return await resp.json()
                except Exception:  # noqa: BLE001
                    return text
        except asyncio.TimeoutError as err:
            raise TextBeeError("Request timeout") from err
        except ClientError as err:
            raise TextBeeError(f"HTTP error: {err}") from err

    async def async_ping(self) -> None:
        """Simple validation call – try to fetch received SMS for some device if possible."""
        # If there are no devices we just ensure auth works by calling /gateway/devices
        devices = await self.async_get_devices()
        if not devices:
            # No devices yet is still fine, auth worked
            return
        # Just test get-received-sms on first device
        first = devices[0]
        dev_id = str(first.get("id") or first.get("_id") or first.get("deviceId"))
        if dev_id:
            await self.async_get_received_sms(dev_id)

    async def async_get_devices(self) -> list[dict[str, Any]]:
        """Return all devices for this API key.

        TextBee docs don't fully document this, but the dashboard/API clearly
        use a `/gateway/devices` endpoint.
        """
        data = await self._request("GET", "/gateway/devices")

        if isinstance(data, dict):
            if "devices" in data and isinstance(data["devices"], list):
                return data["devices"]
            if "data" in data and isinstance(data["data"], list):
                return data["data"]
        if isinstance(data, list):
            return data

        _LOGGER.warning("Unexpected devices payload from TextBee: %s", data)
        return []

    async def async_get_received_sms(self, device_id: str) -> list[dict[str, Any]]:
        """Fetch received SMS via REST.

        GET /gateway/devices/{DEVICE_ID}/get-received-sms
        per the docs / quickstart.
        """
        data = await self._request(
            "GET",
            f"/gateway/devices/{device_id}/get-received-sms",
        )
        if isinstance(data, dict):
            for key in ("data", "messages", "items"):
                val = data.get(key)
                if isinstance(val, list):
                    return val
            return []
        if isinstance(data, list):
            return data
        return []

    async def async_send_sms(
        self,
        device_id: str,
        recipients: list[str],
        message: str,
        extras: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Send SMS or bulk SMS via /send-sms.

        POST /gateway/devices/{DEVICE_ID}/send-sms
        { recipients: ['+1...'], message: '...' }
        """
        payload: dict[str, Any] = {
            "recipients": recipients,
            "message": message,
        }
        if extras:
            payload.update(extras)

        return await self._request(
            "POST",
            f"/gateway/devices/{device_id}/send-sms",
            json=payload,
        )

    async def async_send_mms(
        self,
        device_id: str,
        recipients: list[str],
        message: str,
        media_urls: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Attempt to send 'picture text' (media attached).

        TextBee's official docs only mention SMS, but if/when MMS
        is supported, it's very likely under a media_urls/mediaUrls
        field. We'll send it if provided.
        """
        extras: dict[str, Any] = {}
        if media_urls:
            extras["media_urls"] = media_urls
        return await self.async_send_sms(device_id, recipients, message, extras=extras)

    async def async_get_sms_by_id(
        self, device_id: str, sms_id: str
    ) -> dict[str, Any] | None:
        """Retrieve details and status of a specific SMS.

        GET /gateway/devices/:id/sms/:smsId
        """
        data = await self._request(
            "GET",
            f"/gateway/devices/{device_id}/sms/{sms_id}",
        )
        if isinstance(data, dict):
            return data.get("data") or data
        return None
