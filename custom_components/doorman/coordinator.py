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
        self.has_write_permission: bool = True
        self._log_buffer: list[dict[str, Any]] = []
        self._log_buffer_max = 200

    async def async_init_device_info(self) -> None:
        """Fetch static device information and check write permissions at startup."""
        self.device_info = await self.client.get_system_info()
        await self.client.load_dir_template()
        self.has_write_permission = await self.client.check_directory_write_permission()
        if not self.has_write_permission:
            _LOGGER.warning(
                "Doorman: directory write is unavailable for the API user. "
                "Create/update/delete operations will fail. "
                "This may be a firmware limitation (the Directory service was added to the "
                "2N HTTP API in a later firmware version). Check for a firmware update, "
                "or enable Directory write access in: Settings → Services → HTTP API → Users."
            )

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

        # Accumulate events into the rolling buffer (newest first, capped at max)
        if log_events:
            self._log_buffer = (log_events + self._log_buffer)[: self._log_buffer_max]

        return {
            "users": users,
            "switches": switches,
            "log_events": self._log_buffer,
            "has_write_permission": self.has_write_permission,
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
