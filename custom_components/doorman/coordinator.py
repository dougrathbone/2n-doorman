"""DataUpdateCoordinator for Doorman."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import DoormanApiError, DoormanAuthError, TwoNApiClient
from .const import DEFAULT_POLL_INTERVAL, DOMAIN

_LOGGER = logging.getLogger(__name__)

# 2N log event types that indicate an access attempt
ACCESS_EVENTS = {
    "UserAuthenticated",
    "UserRejected",
    "CodeEntered",
    "CardEntered",
    "FingerEntered",
    "MobKeyEntered",
}


class DoormanCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls the 2N device and distributes data to all entities."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: TwoNApiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(
                seconds=entry.options.get(
                    "poll_interval", DEFAULT_POLL_INTERVAL
                )
            ),
        )
        self.client = client
        self.device_info: dict[str, Any] = {}

    async def async_init_device_info(self) -> None:
        """Fetch static device information once at startup."""
        self.device_info = await self.client.get_system_info()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            users, switches, log_events = await asyncio.gather(
                self.client.query_users(),
                self.client.get_switch_status(),
                self.client.pull_log(),
            )
        except DoormanAuthError as err:
            raise ConfigEntryAuthFailed from err
        except DoormanApiError as err:
            raise UpdateFailed(f"2N API error: {err}") from err

        self._fire_new_access_events(log_events)

        return {
            "users": users,
            "switches": switches,
            "log_events": log_events,
        }

    def _fire_new_access_events(self, events: list[dict[str, Any]]) -> None:
        """Fire HA bus events for log entries returned since the last poll.

        The log subscription on the device tracks the watermark, so every
        event returned here is new — no client-side deduplication needed.
        """
        for event in events:
            event_type = event.get("event", "")
            if event_type in ACCESS_EVENTS:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_access",
                    {
                        "event_type": event_type,
                        "params": event.get("params", {}),
                        "utc_time": event.get("utcTime"),
                    },
                )
