"""DataUpdateCoordinator for Doorman."""
from __future__ import annotations

import asyncio
import contextlib
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
        self.access_points: list[dict[str, Any]] = []
        self.has_write_permission: bool = True
        self._log_buffer: list[dict[str, Any]] = []
        self._log_buffer_max = 200
        self._last_access: dict[str, str] = {}
        self._pending_access_saves: list[tuple[str, str]] = []
        self._log_task: asyncio.Task | None = None

    async def async_init_device_info(self) -> None:
        """Fetch static device information and check write permissions at startup."""
        self.device_info = await self.client.get_system_info()
        await self.client.load_dir_template()
        self.has_write_permission = await self.client.check_directory_write_permission()
        self.access_points: list[dict[str, Any]] = await self.client.get_access_point_caps()
        if not self.has_write_permission:
            _LOGGER.warning(
                "Doorman: directory write is unavailable for the API user. "
                "Create/update/delete operations will fail. "
                "This may be a firmware limitation (the Directory service was added to the "
                "2N HTTP API in a later firmware version). Check for a firmware update, "
                "or enable Directory write access in: Settings → Services → HTTP API → Users."
            )

    def start_log_listener(self) -> None:
        """Start the background long-poll log listener task."""
        if self._log_task and not self._log_task.done():
            return
        self._log_task = self.hass.async_create_background_task(
            self._log_listener_loop(),
            name=f"doorman_log_listener_{self.config_entry.entry_id}",
        )

    async def async_shutdown(self) -> None:
        """Cancel the log listener task on unload."""
        if self._log_task and not self._log_task.done():
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
        await super().async_shutdown()

    async def _log_listener_loop(self) -> None:
        """Long-poll the device log and fire HA events as they arrive.

        Uses a 20 s server-side timeout so events are surfaced within 20 s
        rather than waiting for the next scheduled coordinator poll.  The loop
        runs indefinitely; transient errors trigger a 5 s back-off.
        """
        while True:
            try:
                events = await self.client.pull_log(server_timeout=20)
            except asyncio.CancelledError:
                return
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Doorman log listener error (%s). Retrying in 5 s.", err, exc_info=True
                )
                await asyncio.sleep(5)
                continue

            if not events:
                continue

            self._fire_new_access_events(events)
            self._log_buffer = (events + self._log_buffer)[: self._log_buffer_max]

            # Persist last_access entries collected by _fire_new_access_events
            if self._pending_access_saves:
                store = self.hass.data.get(f"{DOMAIN}_store")
                if store:
                    saved = list(self._pending_access_saves)
                    self._pending_access_saves.clear()
                    for uuid, utc_time in saved:
                        await store.update_last_access(uuid, utc_time)
                else:
                    self._pending_access_saves.clear()

            # Push an update to all listeners so the log tab refreshes immediately
            if self.data is not None:
                self.async_set_updated_data(
                    {**self.data, "log_events": self._log_buffer, "last_access": self._last_access}
                )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            users, switches = await asyncio.gather(
                self.client.query_users(),
                self.client.get_switch_status(),
            )
        except DoormanAuthError as err:
            raise ConfigEntryAuthFailed from err
        except DoormanApiError as err:
            raise UpdateFailed(f"2N API error: {err}") from err

        return {
            "users": users,
            "switches": switches,
            "log_events": self._log_buffer,
            "has_write_permission": self.has_write_permission,
            "last_access": self._last_access,
        }

    def _fire_new_access_events(self, events: list[dict[str, Any]]) -> None:
        """Fire HA bus events for log entries returned since the last poll."""
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
            if event_type == "UserAuthenticated":
                params = event.get("params", {})
                user_info = params.get("user", {})
                user_uuid = user_info.get("uuid") or user_info.get("id")
                utc_time = event.get("utcTime")
                if user_uuid and utc_time:
                    self._last_access[str(user_uuid)] = utc_time
                    self._pending_access_saves.append((str(user_uuid), utc_time))
