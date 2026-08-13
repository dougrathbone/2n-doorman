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
from .const import (
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    LOG_BACKFILL_MAX_PULLS,
    LOG_BACKFILL_SECONDS,
)
from .storage import AccessLogStore

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

# 2N devices intermittently return 401/timeout on polls when the digest
# nonce rotates or the device is briefly busy. Re-authentication on the
# next request usually succeeds, so we only surface a re-auth flow after
# this many consecutive auth failures — otherwise users get spurious
# "could not authenticate" repair issues for transient hiccups.
AUTH_FAILURE_THRESHOLD = 3

# Log-listener back-off: start at 5 s, double on each consecutive error,
# cap at 60 s, reset on the first successful pull.
LOG_LISTENER_INITIAL_BACKOFF = 5
LOG_LISTENER_MAX_BACKOFF = 60


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
        # Durable access-log history for this entry — survives restarts and
        # reloads, unlike the in-memory buffer it replaces.
        self.log_store = AccessLogStore(hass, entry.entry_id)
        self._last_access: dict[str, int] = {}
        self._pending_access_saves: list[tuple[str, int]] = []
        self._log_task: asyncio.Task | None = None
        self._consecutive_auth_failures = 0
        self._consecutive_listener_auth_failures = 0

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

    async def async_load_access_log(self) -> None:
        """Load this entry's persisted access-log history.

        A corrupt or unreadable history must not stop the integration from
        loading — the log is a convenience record, not operational state.
        """
        try:
            await self.log_store.async_load()
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Doorman: could not load the stored access log — starting empty",
                exc_info=True,
            )

    async def async_backfill_access_log(self) -> int:
        """Merge the device's on-box log history into the stored log.

        Runs once at setup. This deliberately does NOT fire ``doorman_access``
        bus events and does not touch ``_last_access``: backfilled entries are
        historical, and replaying them through the notification path would
        spam every notify target with stale door events after each restart.
        Only the live listener notifies.

        Returns the number of events added (0 if the device cannot serve
        history — backfill is best-effort and never fails setup).
        """
        try:
            history = await self.client.fetch_log_history(
                seconds=LOG_BACKFILL_SECONDS, max_pulls=LOG_BACKFILL_MAX_PULLS
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Doorman: access-log backfill unavailable (%r)", err)
            return 0

        added = self.log_store.add_events(history)
        if added:
            _LOGGER.debug("Doorman: backfilled %d historical log event(s)", added)
        return added

    def start_log_listener(self) -> None:
        """Start the background long-poll log listener task."""
        if self._log_task and not self._log_task.done():
            return
        self._log_task = self.hass.async_create_background_task(
            self._log_listener_loop(),
            name=f"doorman_log_listener_{self.config_entry.entry_id}",
        )

    async def async_shutdown(self) -> None:
        """Cancel the log listener task and flush the access log on unload."""
        if self._log_task and not self._log_task.done():
            self._log_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._log_task
        try:
            await self.log_store.async_flush()
        except Exception:  # noqa: BLE001
            _LOGGER.warning(
                "Doorman: could not persist the access log on shutdown", exc_info=True
            )
        await super().async_shutdown()

    async def _log_listener_loop(self) -> None:
        """Long-poll the device log and fire HA events as they arrive.

        Uses a 20 s server-side timeout so events are surfaced within 20 s
        rather than waiting for the next scheduled coordinator poll. Known
        transient errors back off exponentially (5 → 60 s); unexpected
        errors include a traceback. Backoff resets on the first success.

        The post-pull work (event firing, persistence, listener updates) is
        inside the try so one failure there (e.g. a store write error) backs
        off and retries instead of permanently killing the listener — which
        would silently stop all access events until the next reload.
        """
        backoff = LOG_LISTENER_INITIAL_BACKOFF
        while True:
            try:
                events = await self.client.pull_log(server_timeout=20)

                backoff = LOG_LISTENER_INITIAL_BACKOFF
                self._consecutive_listener_auth_failures = 0
                if not events:
                    continue

                self._fire_new_access_events(events)
                # Debounced: a burst from one pull costs a single disk write.
                self.log_store.add_events(events)

                # Persist last_access entries collected by _fire_new_access_events.
                # Coalesce into a single disk write rather than one per event.
                if self._pending_access_saves:
                    store = self.hass.data.get(f"{DOMAIN}_store")
                    saved = list(self._pending_access_saves)
                    self._pending_access_saves.clear()
                    if store:
                        await store.update_last_access_batch(saved)

                # Push an update to all listeners so the log tab refreshes immediately
                if self.data is not None:
                    self.async_set_updated_data(
                        {
                            **self.data,
                            "log_events": self.log_store.events,
                            "last_access": self._last_access,
                        }
                    )
            except asyncio.CancelledError:
                return
            except DoormanAuthError as err:
                # Distinct from generic API errors: a persistent auth failure
                # here would otherwise log a warning every backoff cycle forever
                # while never triggering the re-auth flow (only the poll path
                # escalates). After repeated failures, start re-authentication
                # and stop the listener — it will be restarted on reload.
                self._consecutive_listener_auth_failures += 1
                if self._consecutive_listener_auth_failures >= AUTH_FAILURE_THRESHOLD:
                    _LOGGER.error(
                        "Doorman log listener: authentication failed %d times — "
                        "starting re-authentication",
                        self._consecutive_listener_auth_failures,
                    )
                    self.config_entry.async_start_reauth(self.hass)
                    return
                _LOGGER.warning(
                    "Doorman log listener: transient auth error (%s) — retrying in %d s",
                    err, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, LOG_LISTENER_MAX_BACKOFF)
            except (DoormanApiError, TimeoutError) as err:
                _LOGGER.warning(
                    "Doorman log listener: %s — retrying in %d s",
                    err.__class__.__name__ + (f": {err}" if str(err) else ""),
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, LOG_LISTENER_MAX_BACKOFF)
            except Exception as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Doorman log listener: unexpected error (%r) — retrying in %d s",
                    err, backoff, exc_info=True,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, LOG_LISTENER_MAX_BACKOFF)

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            users, switches = await asyncio.gather(
                self.client.query_users(),
                self.client.get_switch_status(),
            )
        except DoormanAuthError as err:
            self._consecutive_auth_failures += 1
            if self._consecutive_auth_failures >= AUTH_FAILURE_THRESHOLD:
                raise ConfigEntryAuthFailed from err
            # Single transient 401s on 2N devices are common (digest nonce
            # rotation, device busy). Surface as UpdateFailed so entities go
            # unavailable for one cycle without triggering a re-auth flow.
            raise UpdateFailed(f"Transient auth error ({err}); will retry") from err
        except DoormanApiError as err:
            raise UpdateFailed(f"2N API error: {err}") from err
        except TimeoutError as err:
            # A bare timeout from the device is not an aiohttp.ClientError, so it
            # escapes _request unwrapped. Surface it cleanly as a retryable failure.
            raise UpdateFailed(f"Timeout talking to 2N device ({err}); will retry") from err

        self._consecutive_auth_failures = 0
        return {
            "users": users,
            "switches": switches,
            "log_events": self.log_store.events,
            "has_write_permission": self.has_write_permission,
            "last_access": self._last_access,
        }

    def _fire_new_access_events(self, events: list[dict[str, Any]]) -> None:
        """Fire HA bus events for log entries returned since the last poll.

        The bus event carries the originating ``entry_id`` so per-entry
        listeners (event entities, per-device UI) can ignore events from
        other coordinators — without an entry_id filter, every event
        entity would fire for every device in a multi-device install.

        ``utcTime`` is epoch seconds (uint32) per the 2N HTTP API and is
        passed through as-is; the panel converts it for display.
        """
        entry_id = self.config_entry.entry_id
        for event in events:
            event_type = event.get("event", "")
            params = event.get("params", {})
            utc_time = event.get("utcTime")
            if event_type in ACCESS_EVENTS:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_access",
                    {
                        "entry_id": entry_id,
                        "event_type": event_type,
                        "params": params,
                        "utc_time": utc_time,
                    },
                )
            if event_type == "UserAuthenticated":
                # 2N places identifiers flat on params (name/uuid), not under
                # a nested "user" object.
                user_uuid = params.get("uuid")
                if user_uuid and utc_time:
                    self._last_access[str(user_uuid)] = utc_time
                    self._pending_access_saves.append((str(user_uuid), utc_time))
