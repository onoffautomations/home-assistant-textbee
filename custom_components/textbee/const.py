from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "textbee"

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.TEXT,
]

CONF_API_KEY = "api_key"
CONF_BASE_URL = "base_url"
CONF_WEBHOOK_ID = "webhook_id"

DEFAULT_BASE_URL = "https://api.textbee.dev/api/v1"

DATA_CLIENT = "client"
DATA_COORDINATOR = "coordinator"

# Polling interval to fetch devices + incoming SMS via API
DEFAULT_POLL_INTERVAL = 15  # seconds
