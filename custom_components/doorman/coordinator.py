"""DataUpdateCoordinator for Doorman."""
from __future__ import annotations

import asyncio
import contextlib
import logging
from datetime import timedelta
from typing import Any, NamedTuple

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import DoormanApiError, DoormanAuthError, TwoNApiClient
from .const import (
    CONF_DOORBELL_KEY_CODE,
    DEFAULT_DOORBELL_KEY_CODE,
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

# Security-relevant 2N log event types — door forced/held open, tamper, etc.
# Fired on the bus like access events so automations can alert on them.
SECURITY_EVENTS = {
    "UnauthorizedDoorOpen",
    "DoorOpenTooLong",
    "TamperSwitchActivated",
    "SwitchesBlocked",
    "SilentAlarm",
    "LoginBlocked",
}

# State-change 2N log event types — fired on the bus AND applied to entity
# state immediately (no waiting for the next scheduled poll).
STATE_EVENTS = {
    "DoorStateChanged",
    "SwitchStateChanged",
    "InputChanged",
    "OutputChanged",
}

# Synthetic event_type emitted on the bus when a KeyPressed matches the
# configured doorbell key. Downstream listeners (event entity, notifications)
# discriminate on this rather than raw "KeyPressed" so keypad digits don't
# accidentally trigger doorbell logic.
DOORBELL_EVENT_TYPE = "DoorbellPressed"

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


class BackfillResult(NamedTuple):
    """Outcome of one access-log backfill run.

    ``fetched`` and ``added`` are reported separately so callers can tell
    "the device served no history at all" (fetched == 0 — most likely the
    firmware does not accept the ``include`` parameter on /api/log/subscribe)
    apart from "the device served history but we already had all of it"
    (fetched > 0, added == 0 — everything working, simply nothing new).
    """

    fetched: int
    added: int


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
        self.camera_caps: dict[str, Any] = {}
        self.io_ports: list[dict[str, Any]] = []
        self.has_write_permission: bool = True
        # Durable access-log history for this entry — survives restarts and
        # reloads, unlike the in-memory buffer it replaces.
        self.log_store = AccessLogStore(hass, entry.entry_id)
        self._last_access: dict[str, int] = {}
        self._pending_access_saves: list[tuple[str, int]] = []
        self._log_task: asyncio.Task | None = None
        # At most one backfill runs at a time: the startup task and every
        # doorman.resync_log_history call share this task.
        self._backfill_task: asyncio.Task[BackfillResult] | None = None
        # Set by async_shutdown. Once the entry is unloading the store must
        # never be written again (a write would resurrect a flushed — or, on
        # entry removal, a deleted — history file).
        self._closed = False
        self._consecutive_auth_failures = 0
        self._consecutive_listener_auth_failures = 0

    async def async_init_device_info(self) -> None:
        """Fetch static device information and check write permissions at startup."""
        self.device_info = await self.client.get_system_info()
        await self.client.load_dir_template()
        self.has_write_permission = await self.client.check_directory_write_permission()
        self.access_points: list[dict[str, Any]] = await self.client.get_access_point_caps()
        self.camera_caps: dict[str, Any] = await self._probe_camera_caps()
        self.io_ports: list[dict[str, Any]] = await self._probe_io_caps()
        if not self.has_write_permission:
            _LOGGER.warning(
                "Doorman: directory write is unavailable for the API user. "
                "Create/update/delete operations will fail. "
                "This may be a firmware limitation (the Directory service was added to the "
                "2N HTTP API in a later firmware version). Check for a firmware update, "
                "or enable Directory write access in: Settings → Services → HTTP API → Users."
            )

    async def _probe_camera_caps(self) -> dict[str, Any]:
        """Fetch camera capabilities, tolerating camera-less devices.

        Keypad-only Access Units (and accounts without the Camera privilege)
        fail this call — that must not block setup of everything else.
        """
        try:
            caps = await self.client.get_camera_caps()
        except (DoormanApiError, TimeoutError) as err:
            _LOGGER.debug("Doorman: camera not available (%s)", err)
            return {}
        if not caps.get("jpegResolution"):
            return {}
        return caps

    async def _probe_io_caps(self) -> list[dict[str, Any]]:
        """Fetch I/O port capabilities, tolerating devices without the I/O service.

        Older firmware returns code 2 (invalid request path) for /api/io/*;
        without caps the coordinator never polls /api/io/status, so such
        devices don't pay for a failing request every cycle.
        """
        try:
            return await self.client.get_io_caps()
        except (DoormanApiError, TimeoutError) as err:
            _LOGGER.debug("Doorman: I/O service not available (%s)", err)
            return []

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

        This deliberately does NOT fire ``doorman_access`` bus events and does
        not touch ``_last_access``: backfilled entries are historical, and
        replaying them through the notification path would spam every notify
        target with stale door events after each restart. Only the live
        listener notifies.

        Returns the number of events added (0 if the device cannot serve
        history — backfill is best-effort and never fails setup).
        """
        return (await self._async_run_backfill()).added

    async def _async_run_backfill(self) -> BackfillResult:
        """Fetch the on-box history and merge it into the store.

        Best-effort: any device error yields ``BackfillResult(0, 0)`` rather
        than raising, so firmware that cannot serve history behaves exactly as
        it did before backfill existed. ``asyncio.CancelledError`` is a
        BaseException and therefore still propagates, which is what lets
        :meth:`async_shutdown` stop a run in flight.
        """
        try:
            history = await self.client.fetch_log_history(
                seconds=LOG_BACKFILL_SECONDS, max_pulls=LOG_BACKFILL_MAX_PULLS
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Doorman: access-log backfill unavailable (%r)", err)
            return BackfillResult(0, 0)

        # The entry may have unloaded while the fetch was in flight. Writing
        # now would mark the store dirty again after async_shutdown flushed it
        # (and could re-create a file async_remove_entry has just deleted).
        if self._closed:
            _LOGGER.debug(
                "Doorman: discarding %d backfilled event(s) — the entry is unloading",
                len(history),
            )
            return BackfillResult(len(history), 0)

        added = self.log_store.add_events(history)
        if added:
            _LOGGER.debug("Doorman: backfilled %d historical log event(s)", added)
            # Push the merged history to the panel/entities straight away
            # instead of leaving it invisible until the next scheduled poll.
            # This is a data update only — no doorman_access events are fired.
            if self.data is not None:
                self.async_set_updated_data(
                    {**self.data, "log_events": self.log_store.events}
                )
        return BackfillResult(len(history), added)

    def _ensure_backfill_task(self) -> asyncio.Task[BackfillResult] | None:
        """Return the running backfill task, starting one if needed.

        Returns None once the entry is unloading. Reusing the in-flight task is
        what keeps a user hammering doorman.resync_log_history — or resyncing
        while the startup backfill is still running — from opening a second
        history subscription on the device.
        """
        if self._closed:
            return None
        if self._backfill_task is None or self._backfill_task.done():
            self._backfill_task = self.hass.async_create_background_task(
                self._async_run_backfill(),
                name=f"doorman_log_backfill_{self.config_entry.entry_id}",
            )
        return self._backfill_task

    def start_backfill(self) -> None:
        """Kick off the one-shot startup backfill in the background.

        Deliberately not awaited by ``async_setup_entry``: a worst-case run is
        a subscribe plus ``LOG_BACKFILL_MAX_PULLS`` pulls against a device that
        may be unresponsive, which would show up as a slow-setup warning and
        leave the integration unavailable in the meantime.
        """
        self._ensure_backfill_task()

    async def async_resync_access_log(self) -> BackfillResult:
        """Run a backfill on demand and report what it did.

        Joins the in-flight run when there is one, so concurrent callers never
        duplicate work. Obeys the same no-notify guarantee as the startup
        backfill.
        """
        task = self._ensure_backfill_task()
        if task is None:
            return BackfillResult(0, 0)
        try:
            # Shielded: if *this* caller goes away the shared run must survive
            # for the other callers awaiting it.
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                # async_shutdown cancelled the run — the entry is unloading and
                # nothing was written. Report an empty result rather than
                # cancelling the (unrelated) service call.
                _LOGGER.debug(
                    "Doorman: access-log resync aborted — the config entry unloaded"
                )
                return BackfillResult(0, 0)
            raise

    def start_log_listener(self) -> None:
        """Start the background long-poll log listener task."""
        if self._log_task and not self._log_task.done():
            return
        self._log_task = self.hass.async_create_background_task(
            self._log_listener_loop(),
            name=f"doorman_log_listener_{self.config_entry.entry_id}",
        )

    async def async_shutdown(self) -> None:
        """Cancel the background tasks and flush the access log on unload."""
        # Order matters. Both background tasks write to log_store, so they are
        # stopped *before* the flush below — otherwise a backfill returning
        # between the flush and async_remove_entry would dirty the store again
        # and its debounced write would re-create the deleted history file.
        # _closed additionally blocks any write from a resync that is somehow
        # still in flight (it is checked immediately before add_events).
        self._closed = True
        if self._backfill_task and not self._backfill_task.done():
            self._backfill_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._backfill_task
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
            coros: list[Any] = [
                self.client.query_users(),
                self.client.get_switch_status(),
            ]
            # Only poll I/O when the caps probe found ports — devices without
            # the I/O service would otherwise error every cycle.
            if self.io_ports:
                coros.append(self.client.get_io_status())
            results = await asyncio.gather(*coros)
            users, switches = results[0], results[1]
            io_status = results[2] if len(results) > 2 else []
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
            "io": io_status,
            "log_events": self.log_store.events,
            "has_write_permission": self.has_write_permission,
            "last_access": self._last_access,
        }

    def _doorbell_key_code(self) -> str:
        """Return the doorbell key configured for this entry.

        Read from DoormanStore on every log batch rather than cached (or read
        from ``entry.options``): saving the setting from the panel must not
        reload the entry, because a reload drops the 2N log subscription and a
        fresh one starts empty with no watermark. Re-reading here means a
        changed key takes effect on the very next pull, with no reload.

        An empty string means "no doorbell key" — see the WS handler docstring.
        """
        store = self.hass.data.get(f"{DOMAIN}_store")
        if store is None:
            return DEFAULT_DOORBELL_KEY_CODE
        return store.get_notification_settings(self.config_entry.entry_id).get(
            CONF_DOORBELL_KEY_CODE, DEFAULT_DOORBELL_KEY_CODE
        )

    def _fire_new_access_events(self, events: list[dict[str, Any]]) -> None:
        """Process log entries returned since the last poll.

        Fires ``doorman_access`` bus events for access, security, and state
        events, and applies state events (switch/door/I-O) to coordinator data
        immediately so entities reflect them without waiting for the next
        scheduled poll.

        The bus event carries the originating ``entry_id`` so per-entry
        listeners (event entities, per-device UI) can ignore events from
        other coordinators — without an entry_id filter, every event
        entity would fire for every device in a multi-device install.

        ``utcTime`` is epoch seconds (uint32) per the 2N HTTP API and is
        passed through as-is; the panel converts it for display.
        """
        entry_id = self.config_entry.entry_id
        doorbell_key = self._doorbell_key_code()
        for event in events:
            event_type = event.get("event", "")
            params = event.get("params", {})
            utc_time = event.get("utcTime")
            if event_type in ACCESS_EVENTS | SECURITY_EVENTS | STATE_EVENTS:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_access",
                    {
                        "entry_id": entry_id,
                        "event_type": event_type,
                        "params": params,
                        "utc_time": utc_time,
                    },
                )
                self._apply_state_event(event_type, params)
            # KeyPressed reports every keypad interaction; only the
            # configured doorbell key fires a doorbell event. An empty
            # doorbell key disables the flow entirely.
            elif (
                event_type == "KeyPressed"
                and doorbell_key
                and params.get("key") == doorbell_key
            ):
                self.hass.bus.async_fire(
                    f"{DOMAIN}_access",
                    {
                        "entry_id": entry_id,
                        "event_type": DOORBELL_EVENT_TYPE,
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

    def _apply_state_event(self, event_type: str, params: dict[str, Any]) -> None:
        """Apply a state-change log event to coordinator data in place.

        The listener's post-pull ``async_set_updated_data`` picks the mutation
        up in the same batch, so entities see the change immediately.
        """
        if self.data is None:
            return
        if event_type == "SwitchStateChanged":
            switch_id = params.get("switch")
            for sw in self.data.get("switches", []):
                if sw.get("id") == switch_id:
                    sw["active"] = bool(params.get("state"))
                    break
        elif event_type in ("InputChanged", "OutputChanged"):
            port = params.get("port")
            for p in self.data.get("io", []):
                if p.get("port") == port:
                    p["state"] = int(bool(params.get("state")))
                    break
