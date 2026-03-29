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
        self._last_log_event_id: str | None = None

    async def async_init_device_info(self) -> None:
        """Fetch static device information once at startup."""
        self.device_info = await self.client.get_system_info()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            users, switches, log_events = await asyncio.gather(
                self.client.query_users(),
                self.client.get_switch_status(),
                self.client.pull_log(count=50),
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
        """Fire HA bus events for any log entries not seen on the previous poll."""
        if not events:
            return

        latest = events[-1]
        latest_id = latest.get("id") or latest.get("utcTime")

        if self._last_log_event_id is None:
            # First poll — record the watermark but don't fire historical events
            self._last_log_event_id = latest_id
            return

        # Collect events that arrived after the last known event
        new_events: list[dict] = []
        for event in reversed(events):
            event_id = event.get("id") or event.get("utcTime")
            if event_id == self._last_log_event_id:
                break
            new_events.append(event)

        self._last_log_event_id = latest_id

        for event in reversed(new_events):
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
