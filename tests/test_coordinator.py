"""Tests for the Doorman coordinator — polling, error handling, and event firing."""
from __future__ import annotations

import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.api_client import DoormanApiError, DoormanAuthError
from custom_components.doorman.const import (
    CONF_DOORBELL_KEY_CODE,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
    MAX_STORED_LOG_EVENTS,
)
from custom_components.doorman.coordinator import DoormanCoordinator
from custom_components.doorman.storage import DoormanStore

from .conftest import MOCK_DEVICE_INFO, MOCK_SWITCHES, MOCK_USERS


def _make_coordinator(
    hass: HomeAssistant, client, entry: MockConfigEntry | None = None
) -> DoormanCoordinator:
    if entry is None:
        entry = MockConfigEntry(
            domain=DOMAIN,
            data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
        )
        entry.add_to_hass(hass)
    return DoormanCoordinator(hass, entry, client)


def _install_store(hass: HomeAssistant) -> DoormanStore:
    """Put a real DoormanStore (no disk) in hass.data and return it."""
    store = DoormanStore(hass)
    store._store = MagicMock()
    store._store.async_save = AsyncMock()
    hass.data[f"{DOMAIN}_store"] = store
    return store


@pytest.mark.asyncio
async def test_coordinator_fetch_returns_users_and_switches(
    hass: HomeAssistant,
) -> None:
    """Coordinator returns users and switches from the device on a successful poll."""
    client = MagicMock()
    client.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)
    client.load_dir_template = AsyncMock(return_value=None)
    client.check_directory_write_permission = AsyncMock(return_value=True)
    client.get_access_point_caps = AsyncMock(return_value=[{"id": 1, "name": "Access point 1"}])
    client.get_camera_caps = AsyncMock(return_value={})
    client.get_io_caps = AsyncMock(return_value=[])
    client.query_users = AsyncMock(return_value=MOCK_USERS)
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_init_device_info()
    await coordinator.async_refresh()

    assert coordinator.data["users"] == MOCK_USERS
    assert coordinator.data["switches"] == MOCK_SWITCHES
    assert coordinator.device_info == MOCK_DEVICE_INFO
    assert coordinator.data["has_write_permission"] is True


@pytest.mark.asyncio
async def test_coordinator_poll_does_not_call_pull_log(
    hass: HomeAssistant,
) -> None:
    """_async_update_data no longer calls pull_log — the background task owns it."""
    client = MagicMock()
    client.query_users = AsyncMock(return_value=MOCK_USERS)
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
    client.pull_log = AsyncMock(return_value=[])

    coordinator = _make_coordinator(hass, client)
    await coordinator._async_update_data()

    client.pull_log.assert_not_called()


@pytest.mark.asyncio
async def test_coordinator_single_auth_error_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """A single transient auth error surfaces as UpdateFailed, not re-auth.

    2N devices intermittently return 401 (digest nonce rotation, device
    briefly busy) and recover on the next poll, so we shouldn't trigger a
    re-auth flow on a one-off failure.
    """
    client = MagicMock()
    client.query_users = AsyncMock(side_effect=DoormanAuthError("transient"))
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed, match="Transient auth error"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_persistent_auth_errors_trigger_reauth(
    hass: HomeAssistant,
) -> None:
    """After AUTH_FAILURE_THRESHOLD consecutive auth errors, escalate to ConfigEntryAuthFailed."""
    from custom_components.doorman.coordinator import AUTH_FAILURE_THRESHOLD

    client = MagicMock()
    client.query_users = AsyncMock(side_effect=DoormanAuthError("expired"))
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)

    for _ in range(AUTH_FAILURE_THRESHOLD - 1):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_auth_failure_counter_resets_on_success(
    hass: HomeAssistant,
) -> None:
    """A successful poll between failures resets the consecutive-failure counter."""
    from custom_components.doorman.coordinator import AUTH_FAILURE_THRESHOLD

    client = MagicMock()
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)

    # Fail threshold-1 times, then succeed once, then fail once more — should
    # surface as UpdateFailed (not ConfigEntryAuthFailed) because the counter reset.
    fail_then_succeed_then_fail = (
        [DoormanAuthError("transient")] * (AUTH_FAILURE_THRESHOLD - 1)
        + [MOCK_USERS]
        + [DoormanAuthError("transient")]
    )
    client.query_users = AsyncMock(side_effect=fail_then_succeed_then_fail)

    for _ in range(AUTH_FAILURE_THRESHOLD - 1):
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
    await coordinator._async_update_data()
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_api_error_raises_update_failed(
    hass: HomeAssistant,
) -> None:
    """A generic API error wraps as UpdateFailed so HA shows an unavailable state."""
    client = MagicMock()
    client.query_users = AsyncMock(side_effect=DoormanApiError("timeout"))
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_coordinator_fires_ha_bus_events_for_new_log_entries(
    hass: HomeAssistant,
) -> None:
    """_fire_new_access_events publishes doorman_access bus events for each relevant entry.

    Events are now delivered by the background long-poll task; this test
    exercises the firing logic directly to keep it fast and deterministic.
    """
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    new_event = {
        "id": "evt-002",
        "event": "CardEntered",
        "utcTime": 1743242700,
        "params": {"uid": "AABBCCDD", "valid": True},
    }
    coordinator._fire_new_access_events([new_event])
    await hass.async_block_till_done()

    assert len(fired_events) == 1
    assert fired_events[0].data["event_type"] == "CardEntered"
    # entry_id must be included so per-entity listeners can filter on it
    assert fired_events[0].data["entry_id"] == coordinator.config_entry.entry_id


@pytest.mark.asyncio
async def test_coordinator_no_events_when_fire_called_with_empty_list(
    hass: HomeAssistant,
) -> None:
    """No bus events are fired when _fire_new_access_events receives an empty list."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    coordinator._fire_new_access_events([])
    await hass.async_block_till_done()

    assert len(fired_events) == 0


@pytest.mark.asyncio
async def test_fire_new_access_events_tracks_last_access(
    hass: HomeAssistant,
) -> None:
    """UserAuthenticated events update _last_access and queue a persistence save."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    event = {
        "id": "evt-003",
        "event": "UserAuthenticated",
        "utcTime": 1743246000,
        "params": {"ap": 0, "session": 3, "name": "Jane", "uuid": "uuid-jane"},
    }
    coordinator._fire_new_access_events([event])

    assert coordinator._last_access.get("uuid-jane") == 1743246000
    assert ("uuid-jane", 1743246000) in coordinator._pending_access_saves


@pytest.mark.asyncio
async def test_key_pressed_with_doorbell_key_fires_doorbell_event(
    hass: HomeAssistant,
) -> None:
    """KeyPressed on the default doorbell key (%1) fires a DoorbellPressed bus event."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    coordinator._fire_new_access_events(
        [{"id": "e-db", "event": "KeyPressed", "utcTime": 1743250000, "params": {"key": "%1"}}]
    )
    await hass.async_block_till_done()

    assert len(fired) == 1
    assert fired[0].data["event_type"] == "DoorbellPressed"
    assert fired[0].data["entry_id"] == coordinator.config_entry.entry_id
    assert fired[0].data["params"] == {"key": "%1"}


@pytest.mark.asyncio
async def test_key_pressed_on_non_doorbell_key_does_not_fire(
    hass: HomeAssistant,
) -> None:
    """A keypad digit press (KeyPressed with key='5') is ignored — not a doorbell."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    coordinator._fire_new_access_events(
        [{"id": "e-key", "event": "KeyPressed", "utcTime": 1743250100, "params": {"key": "5"}}]
    )
    await hass.async_block_till_done()

    assert fired == []


@pytest.mark.asyncio
async def test_doorbell_key_code_is_configurable_via_store(
    hass: HomeAssistant,
) -> None:
    """The stored doorbell_key_code decides which KeyPressed value fires the doorbell."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)
    store = _install_store(hass)
    await store.set_notification_settings(
        coordinator.config_entry.entry_id, {CONF_DOORBELL_KEY_CODE: "%2"}
    )

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    coordinator._fire_new_access_events(
        [
            {"id": "e1", "event": "KeyPressed", "utcTime": 1743250200, "params": {"key": "%1"}},
            {"id": "e2", "event": "KeyPressed", "utcTime": 1743250201, "params": {"key": "%2"}},
        ]
    )
    await hass.async_block_till_done()

    assert len(fired) == 1
    assert fired[0].data["event_type"] == "DoorbellPressed"
    assert fired[0].data["params"] == {"key": "%2"}


@pytest.mark.asyncio
async def test_doorbell_key_change_applies_without_reload(
    hass: HomeAssistant,
) -> None:
    """A doorbell key saved mid-flight takes effect on the next log batch.

    The coordinator re-reads the key from the store per batch precisely so
    saving from the panel never has to reload the entry (a reload drops the
    2N log subscription and loses events).
    """
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)
    store = _install_store(hass)
    entry_id = coordinator.config_entry.entry_id

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    press_2 = [{"id": "e", "event": "KeyPressed", "utcTime": 1, "params": {"key": "%2"}}]

    # Default is %1, so a %2 press is ignored…
    coordinator._fire_new_access_events(press_2)
    await hass.async_block_till_done()
    assert fired == []

    # …until the key is changed in the store — same coordinator object, no reload.
    await store.set_notification_settings(entry_id, {CONF_DOORBELL_KEY_CODE: "%2"})
    coordinator._fire_new_access_events(press_2)
    await hass.async_block_till_done()

    assert len(fired) == 1
    assert fired[0].data["event_type"] == "DoorbellPressed"


@pytest.mark.asyncio
async def test_empty_doorbell_key_disables_the_doorbell(
    hass: HomeAssistant,
) -> None:
    """An empty doorbell_key_code means "no doorbell button" — nothing fires."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)
    store = _install_store(hass)
    await store.set_notification_settings(
        coordinator.config_entry.entry_id, {CONF_DOORBELL_KEY_CODE: ""}
    )

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    coordinator._fire_new_access_events(
        [
            {"id": "e1", "event": "KeyPressed", "utcTime": 1, "params": {"key": "%1"}},
            # A device that reports an empty key must not be treated as a match.
            {"id": "e2", "event": "KeyPressed", "utcTime": 2, "params": {"key": ""}},
        ]
    )
    await hass.async_block_till_done()

    assert fired == []


@pytest.mark.asyncio
async def test_log_events_accumulate_in_the_persistent_store(
    hass: HomeAssistant,
) -> None:
    """Events pulled by the listener accumulate in the durable access-log store."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    coordinator.log_store.add_events(
        [{"id": 1, "event": "CardEntered", "utcTime": 1743242400, "params": {}}]
    )
    coordinator.log_store.add_events(
        [{"id": 2, "event": "UserRejected", "utcTime": 1743242460, "params": {}}]
    )

    assert [e["id"] for e in coordinator.log_store.events] == [1, 2]


@pytest.mark.asyncio
async def test_stored_log_is_capped(hass: HomeAssistant) -> None:
    """The stored history never exceeds MAX_STORED_LOG_EVENTS entries."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    overflow = [
        {"id": i, "event": "CardEntered", "utcTime": 1743242400 + i, "params": {}}
        for i in range(MAX_STORED_LOG_EVENTS + 50)
    ]
    coordinator.log_store.add_events(overflow)

    assert len(coordinator.log_store.events) == MAX_STORED_LOG_EVENTS
    # Oldest trimmed first — the newest event is still present
    assert coordinator.log_store.events[-1]["id"] == MAX_STORED_LOG_EVENTS + 49


@pytest.mark.asyncio
async def test_start_log_listener_is_idempotent(
    hass: HomeAssistant,
) -> None:
    """Calling start_log_listener twice does not create a second task."""
    import asyncio

    async def _never_ending():
        await asyncio.sleep(9999)

    client = MagicMock()
    coordinator = _make_coordinator(hass, client)
    coordinator._log_listener_loop = _never_ending  # type: ignore[method-assign]

    coordinator.start_log_listener()
    task_1 = coordinator._log_task

    coordinator.start_log_listener()
    task_2 = coordinator._log_task

    assert task_1 is task_2  # same task object, no duplicate

    task_1.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task_1


@pytest.mark.asyncio
async def test_async_shutdown_cancels_log_task(
    hass: HomeAssistant,
) -> None:
    """async_shutdown cancels the background log task cleanly."""
    import asyncio

    async def _never_ending():
        await asyncio.sleep(9999)

    client = MagicMock()
    client.async_close = AsyncMock()
    coordinator = _make_coordinator(hass, client)
    coordinator._log_listener_loop = _never_ending  # type: ignore[method-assign]

    coordinator.start_log_listener()
    assert coordinator._log_task is not None
    assert not coordinator._log_task.done()

    await coordinator.async_shutdown()

    assert coordinator._log_task.done()


@pytest.mark.asyncio
async def test_coordinator_timeout_raises_update_failed(hass: HomeAssistant) -> None:
    """A bare TimeoutError from the device surfaces as a retryable UpdateFailed."""
    client = MagicMock()
    client.query_users = AsyncMock(side_effect=TimeoutError("slow"))
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)
    with pytest.raises(UpdateFailed, match="Timeout"):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_log_listener_escalates_persistent_auth_to_reauth(
    hass: HomeAssistant, monkeypatch
) -> None:
    """Repeated auth failures in the listener trigger re-auth instead of looping forever."""
    import custom_components.doorman.coordinator as coord_mod

    client = MagicMock()
    client.pull_log = AsyncMock(side_effect=DoormanAuthError("bad creds"))

    coordinator = _make_coordinator(hass, client)
    coordinator.config_entry.async_start_reauth = MagicMock()
    monkeypatch.setattr(coord_mod.asyncio, "sleep", AsyncMock())

    # Loop returns once the failure threshold is hit and re-auth is started.
    await coordinator._log_listener_loop()

    coordinator.config_entry.async_start_reauth.assert_called_once()
    assert client.pull_log.call_count == coord_mod.AUTH_FAILURE_THRESHOLD


@pytest.mark.asyncio
async def test_log_listener_survives_store_save_failure(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A failing store write must not kill the background listener.

    Regression: post-pull work ran outside the loop's try/except, so one
    exception (e.g. a disk error in update_last_access_batch) terminated the
    listener task and access events silently stopped until reload.
    """
    import asyncio

    import custom_components.doorman.coordinator as coord_mod

    store = MagicMock()
    store.update_last_access_batch = AsyncMock(
        side_effect=[OSError("disk full"), None]
    )
    hass.data[f"{DOMAIN}_store"] = store

    event_a = {
        "id": "evt-a",
        "event": "UserAuthenticated",
        "utcTime": 1743242400,
        "params": {"ap": 0, "session": 1, "name": "A", "uuid": "uuid-a"},
    }
    event_b = {
        "id": "evt-b",
        "event": "UserAuthenticated",
        "utcTime": 1743242460,
        "params": {"ap": 0, "session": 1, "name": "B", "uuid": "uuid-b"},
    }

    pull_calls = 0

    async def fake_pull_log(server_timeout: int = 0) -> list[dict]:
        nonlocal pull_calls
        pull_calls += 1
        if pull_calls == 1:
            return [event_a]
        if pull_calls == 2:
            return [event_b]
        raise asyncio.CancelledError  # stop the loop after both batches

    client = MagicMock()
    client.pull_log = fake_pull_log
    coordinator = _make_coordinator(hass, client)
    monkeypatch.setattr(coord_mod.asyncio, "sleep", AsyncMock())

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    await coordinator._log_listener_loop()
    await hass.async_block_till_done()

    # Both batches fired bus events — the listener kept running after the
    # first store save blew up. Delivery order is not guaranteed when bus
    # jobs are scheduled across a mocked sleep, so compare unordered.
    assert sorted(e.data["params"]["uuid"] for e in fired_events) == ["uuid-a", "uuid-b"]
    assert store.update_last_access_batch.await_count == 2


# ─── Persistent access log ───────────────────────────────────────────────────


def _log_event(event_id: int, utc_time: int, name: str = "UserAuthenticated") -> dict:
    return {
        "id": event_id,
        "event": name,
        "utcTime": utc_time,
        "params": {"ap": 0, "name": "Jane", "uuid": "uuid-jane"},
    }


@pytest.mark.asyncio
async def test_access_log_survives_a_coordinator_restart(hass: HomeAssistant) -> None:
    """Events delivered before a restart are still there afterwards."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)

    client = MagicMock()
    client.query_users = AsyncMock(return_value=MOCK_USERS)
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client, entry)
    await coordinator.async_load_access_log()
    coordinator.log_store.add_events([_log_event(1, 1743242400), _log_event(2, 1743242460)])
    await coordinator.async_shutdown()

    # "Restart": a brand new coordinator for the same config entry
    restarted = _make_coordinator(hass, client, entry)
    await restarted.async_load_access_log()
    data = await restarted._async_update_data()

    assert [e["id"] for e in data["log_events"]] == [1, 2]


@pytest.mark.asyncio
async def test_backfill_populates_log_without_firing_bus_events(
    hass: HomeAssistant,
) -> None:
    """Historical events are recorded but must never notify."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(
        return_value=[_log_event(1, 1743242400), _log_event(2, 1743242460)]
    )

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    added = await coordinator.async_backfill_access_log()
    await hass.async_block_till_done()

    assert added == 2
    assert [e["id"] for e in coordinator.log_store.events] == [1, 2]
    assert fired_events == []
    # Backfill must not rewrite "last access" state either — that is derived
    # from live events only.
    assert coordinator._last_access == {}


@pytest.mark.asyncio
async def test_backfill_does_not_ring_the_doorbell(hass: HomeAssistant) -> None:
    """A historical KeyPressed on the doorbell key must not fire DoorbellPressed.

    The doorbell is emitted from inside _fire_new_access_events, which only the
    live listener calls — so replaying a week of history at startup can never
    ring anyone's phone.
    """
    client = MagicMock()
    client.fetch_log_history = AsyncMock(
        return_value=[
            {
                "id": 7,
                "event": "KeyPressed",
                "utcTime": 1743242400,
                "params": {"key": "%1"},
            }
        ]
    )

    coordinator = _make_coordinator(hass, client)
    _install_store(hass)
    await coordinator.async_load_access_log()

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    added = await coordinator.async_backfill_access_log()
    await hass.async_block_till_done()

    assert added == 1
    assert [e["id"] for e in coordinator.log_store.events] == [7]
    assert fired == []


@pytest.mark.asyncio
async def test_backfill_is_idempotent(hass: HomeAssistant) -> None:
    """Running backfill twice does not duplicate rows."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(
        return_value=[_log_event(1, 1743242400), _log_event(2, 1743242460)]
    )

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    assert await coordinator.async_backfill_access_log() == 2
    assert await coordinator.async_backfill_access_log() == 0
    assert len(coordinator.log_store.events) == 2


@pytest.mark.asyncio
async def test_backfill_does_not_duplicate_already_persisted_events(
    hass: HomeAssistant,
) -> None:
    """After a restart, backfill merges with what is already on disk."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)

    client = MagicMock()
    client.fetch_log_history = AsyncMock(
        return_value=[_log_event(1, 1743242400), _log_event(2, 1743242460)]
    )

    coordinator = _make_coordinator(hass, client, entry)
    await coordinator.async_load_access_log()
    coordinator.log_store.add_events([_log_event(1, 1743242400)])
    await coordinator.async_shutdown()

    restarted = _make_coordinator(hass, client, entry)
    await restarted.async_load_access_log()
    added = await restarted.async_backfill_access_log()

    assert added == 1
    assert [e["id"] for e in restarted.log_store.events] == [1, 2]


@pytest.mark.asyncio
async def test_backfill_degrades_gracefully_when_unsupported(
    hass: HomeAssistant,
) -> None:
    """A device that rejects the history request leaves the log untouched."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(side_effect=DoormanApiError("API error 12", code=12))

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    assert await coordinator.async_backfill_access_log() == 0
    assert coordinator.log_store.events == []


@pytest.mark.asyncio
async def test_live_events_still_fire_and_are_persisted(
    hass: HomeAssistant, monkeypatch
) -> None:
    """The live path keeps firing bus events and now also stores them."""
    import asyncio

    import custom_components.doorman.coordinator as coord_mod

    pull_calls = 0

    async def fake_pull_log(server_timeout: int = 0) -> list[dict]:
        nonlocal pull_calls
        pull_calls += 1
        if pull_calls == 1:
            return [_log_event(7, 1743242400, "CardEntered")]
        raise asyncio.CancelledError

    client = MagicMock()
    client.pull_log = fake_pull_log
    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    coordinator.data = {"users": [], "switches": []}
    monkeypatch.setattr(coord_mod.asyncio, "sleep", AsyncMock())

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    await coordinator._log_listener_loop()
    await hass.async_block_till_done()

    assert [e.data["event_type"] for e in fired_events] == ["CardEntered"]
    assert [e["id"] for e in coordinator.log_store.events] == [7]
    assert coordinator.data["log_events"] == coordinator.log_store.events


@pytest.mark.asyncio
async def test_burst_of_events_triggers_one_save(
    hass: HomeAssistant, monkeypatch
) -> None:
    """A pull carrying many events coalesces into a single delayed disk write."""
    import asyncio

    import custom_components.doorman.coordinator as coord_mod

    burst = [_log_event(i, 1743242400 + i, "CardEntered") for i in range(10)]
    pull_calls = 0

    async def fake_pull_log(server_timeout: int = 0) -> list[dict]:
        nonlocal pull_calls
        pull_calls += 1
        if pull_calls == 1:
            return burst
        raise asyncio.CancelledError

    client = MagicMock()
    client.pull_log = fake_pull_log
    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    monkeypatch.setattr(coord_mod.asyncio, "sleep", AsyncMock())

    with (
        patch.object(coordinator.log_store._store, "async_delay_save") as delay_save,
        patch.object(
            coordinator.log_store._store, "async_save", new=AsyncMock()
        ) as save_now,
    ):
        await coordinator._log_listener_loop()
        await hass.async_block_till_done()

        assert delay_save.call_count == 1
        save_now.assert_not_called()

    assert len(coordinator.log_store.events) == 10


@pytest.mark.asyncio
async def test_shutdown_flushes_pending_access_log_writes(
    hass: HomeAssistant,
) -> None:
    """Unloading the entry persists events still sitting in the debounce window."""
    client = MagicMock()
    client.async_close = AsyncMock()
    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    coordinator.log_store.add_events([_log_event(1, 1743242400)])

    with patch.object(
        coordinator.log_store._store, "async_save", new=AsyncMock()
    ) as save_now:
        await coordinator.async_shutdown()
        assert save_now.await_count == 1


@pytest.mark.asyncio
async def test_two_entries_keep_separate_access_logs(hass: HomeAssistant) -> None:
    """Histories are keyed by entry_id and never merged across devices."""
    entry_a = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.200", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
    )
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)

    client = MagicMock()
    coord_a = _make_coordinator(hass, client, entry_a)
    coord_b = _make_coordinator(hass, client, entry_b)
    await coord_a.async_load_access_log()
    await coord_b.async_load_access_log()

    coord_a.log_store.add_events([_log_event(1, 1743242400)])
    coord_b.log_store.add_events([_log_event(1, 1743242400), _log_event(2, 1743242460)])

    assert len(coord_a.log_store.events) == 1
    assert len(coord_b.log_store.events) == 2


# ─── Backfill as a background task ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_backfill_runs_off_the_calling_path(hass: HomeAssistant) -> None:
    """start_backfill returns immediately; the work happens in a background task."""
    import asyncio

    release = asyncio.Event()

    async def slow_history(**_kwargs) -> list[dict]:
        await release.wait()
        return [_log_event(1, 1743242400)]

    client = MagicMock()
    client.fetch_log_history = slow_history

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    coordinator.start_backfill()
    # Still in flight — nothing stored yet, but the caller was not blocked.
    assert coordinator.log_store.events == []
    assert coordinator._backfill_task is not None
    assert not coordinator._backfill_task.done()
    assert coordinator.config_entry.entry_id in coordinator._backfill_task.get_name()

    release.set()
    await coordinator._backfill_task

    assert [e["id"] for e in coordinator.log_store.events] == [1]


@pytest.mark.asyncio
async def test_start_backfill_is_not_started_twice(hass: HomeAssistant) -> None:
    """A second start_backfill while one is in flight reuses the running task."""
    import asyncio

    release = asyncio.Event()
    calls = 0

    async def slow_history(**_kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        await release.wait()
        return []

    client = MagicMock()
    client.fetch_log_history = slow_history

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    coordinator.start_backfill()
    first = coordinator._backfill_task
    coordinator.start_backfill()

    assert coordinator._backfill_task is first
    release.set()
    await first
    assert calls == 1


@pytest.mark.asyncio
async def test_shutdown_cancels_an_in_flight_backfill(hass: HomeAssistant) -> None:
    """Unloading mid-backfill cancels the task cleanly and stores nothing."""
    import asyncio

    started = asyncio.Event()
    never = asyncio.Event()

    async def hanging_history(**_kwargs) -> list[dict]:
        started.set()
        await never.wait()
        return [_log_event(1, 1743242400)]

    client = MagicMock()
    client.fetch_log_history = hanging_history

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    coordinator.start_backfill()
    await started.wait()

    task = coordinator._backfill_task
    # Must not raise, and must not leave the task running.
    await coordinator.async_shutdown()

    assert task.done()
    assert task.cancelled()
    assert coordinator.log_store.events == []


@pytest.mark.asyncio
async def test_shutdown_cancels_backfill_before_flushing(hass: HomeAssistant) -> None:
    """The flush is the last write: a cancelled backfill cannot dirty the store after it."""
    import asyncio

    order: list[str] = []
    release = asyncio.Event()

    async def slow_history(**_kwargs) -> list[dict]:
        await release.wait()
        order.append("backfill_add")
        return [_log_event(1, 1743242400)]

    client = MagicMock()
    client.fetch_log_history = slow_history

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    coordinator.log_store.add_events([_log_event(9, 1743200000)])
    coordinator.start_backfill()
    await asyncio.sleep(0)

    original_flush = coordinator.log_store.async_flush

    async def tracked_flush() -> None:
        order.append("flush")
        await original_flush()

    with patch.object(coordinator.log_store, "async_flush", tracked_flush):
        await coordinator.async_shutdown()

    # The backfill never reached its store write, so the flush is final.
    assert order == ["flush"]
    release.set()
    await asyncio.sleep(0)
    assert order == ["flush"]


@pytest.mark.asyncio
async def test_backfill_after_shutdown_does_not_write_to_the_store(
    hass: HomeAssistant,
) -> None:
    """A late-returning backfill must not resurrect a flushed/removed store."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(return_value=[_log_event(1, 1743242400)])

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    await coordinator.async_shutdown()

    with patch.object(coordinator.log_store._store, "async_delay_save") as delay_save:
        assert await coordinator.async_backfill_access_log() == 0

    delay_save.assert_not_called()
    assert coordinator.log_store.events == []


@pytest.mark.asyncio
async def test_start_backfill_after_shutdown_is_a_no_op(hass: HomeAssistant) -> None:
    """No new background task is created once the entry has been shut down."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(return_value=[_log_event(1, 1743242400)])

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    await coordinator.async_shutdown()

    coordinator.start_backfill()

    assert coordinator._backfill_task is None
    client.fetch_log_history.assert_not_called()


@pytest.mark.asyncio
async def test_concurrent_resyncs_share_a_single_run(hass: HomeAssistant) -> None:
    """Overlapping resyncs join one run instead of opening two subscriptions."""
    import asyncio

    release = asyncio.Event()
    calls = 0

    async def slow_history(**_kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        await release.wait()
        return [_log_event(1, 1743242400), _log_event(2, 1743242460)]

    client = MagicMock()
    client.fetch_log_history = slow_history

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    first = asyncio.ensure_future(coordinator.async_resync_access_log())
    second = asyncio.ensure_future(coordinator.async_resync_access_log())
    await asyncio.sleep(0)
    release.set()
    results = await asyncio.gather(first, second)

    assert calls == 1
    # Both callers see the same outcome from the shared run.
    assert results[0] == results[1] == (2, 2)
    assert len(coordinator.log_store.events) == 2


@pytest.mark.asyncio
async def test_resync_joins_a_running_startup_backfill(hass: HomeAssistant) -> None:
    """A resync issued while the startup backfill runs waits for it, not a second run."""
    import asyncio

    release = asyncio.Event()
    calls = 0

    async def slow_history(**_kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        await release.wait()
        return [_log_event(1, 1743242400)]

    client = MagicMock()
    client.fetch_log_history = slow_history

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    coordinator.start_backfill()
    await asyncio.sleep(0)

    resync = asyncio.ensure_future(coordinator.async_resync_access_log())
    await asyncio.sleep(0)
    release.set()
    result = await resync

    assert calls == 1
    assert result == (1, 1)


@pytest.mark.asyncio
async def test_resync_reports_fetched_and_added_separately(hass: HomeAssistant) -> None:
    """A device returning known history is distinguishable from one returning none."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(return_value=[_log_event(1, 1743242400)])

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    assert await coordinator.async_resync_access_log() == (1, 1)
    # Second run: the device still serves history, but none of it is new.
    assert await coordinator.async_resync_access_log() == (1, 0)

    # A device that serves nothing at all reports zero fetched.
    client.fetch_log_history = AsyncMock(return_value=[])
    assert await coordinator.async_resync_access_log() == (0, 0)


@pytest.mark.asyncio
async def test_resync_fires_no_bus_events(hass: HomeAssistant) -> None:
    """The no-notify guarantee applies to the on-demand resync too."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(
        return_value=[_log_event(1, 1743242400), _log_event(2, 1743242460)]
    )

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    await coordinator.async_resync_access_log()
    await hass.async_block_till_done()

    assert fired == []
    assert coordinator._last_access == {}


@pytest.mark.asyncio
async def test_backfill_pushes_new_events_to_listeners(hass: HomeAssistant) -> None:
    """Backfilled rows reach the panel without waiting for the next poll."""
    client = MagicMock()
    client.fetch_log_history = AsyncMock(return_value=[_log_event(1, 1743242400)])

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_load_access_log()
    coordinator.data = {"users": [], "switches": [], "log_events": [], "last_access": {}}

    await coordinator.async_resync_access_log()

    assert [e["id"] for e in coordinator.data["log_events"]] == [1]
    assert coordinator.data["users"] == []
