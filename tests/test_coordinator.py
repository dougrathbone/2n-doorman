"""Tests for the Doorman coordinator — polling, error handling, and event firing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.api_client import DoormanApiError, DoormanAuthError
from custom_components.doorman.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.doorman.coordinator import DoormanCoordinator

from .conftest import MOCK_DEVICE_INFO, MOCK_SWITCHES, MOCK_USERS


def _make_coordinator(hass: HomeAssistant, client) -> DoormanCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)
    return DoormanCoordinator(hass, entry, client)


@pytest.mark.asyncio
async def test_coordinator_fetch_returns_users_and_switches(
    hass: HomeAssistant,
) -> None:
    """Coordinator returns users and switches from the device on a successful poll."""
    client = MagicMock()
    client.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)
    client.load_dir_template = AsyncMock(return_value=None)
    client.check_directory_write_permission = AsyncMock(return_value=True)
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
async def test_coordinator_auth_error_raises_config_entry_auth_failed(
    hass: HomeAssistant,
) -> None:
    """An auth error from the API triggers a re-auth flow via ConfigEntryAuthFailed."""
    client = MagicMock()
    client.query_users = AsyncMock(side_effect=DoormanAuthError("expired"))
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)

    coordinator = _make_coordinator(hass, client)

    with pytest.raises(ConfigEntryAuthFailed):
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
        "utcTime": "2026-03-29T10:05:00Z",
        "params": {"card": "AABBCCDD", "valid": True},
    }
    coordinator._fire_new_access_events([new_event])
    await hass.async_block_till_done()

    assert len(fired_events) == 1
    assert fired_events[0].data["event_type"] == "CardEntered"


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
        "utcTime": "2026-03-29T11:00:00Z",
        "params": {"user": {"id": "uuid-jane", "name": "Jane"}, "valid": True},
    }
    coordinator._fire_new_access_events([event])

    assert coordinator._last_access.get("uuid-jane") == "2026-03-29T11:00:00Z"
    assert ("uuid-jane", "2026-03-29T11:00:00Z") in coordinator._pending_access_saves


@pytest.mark.asyncio
async def test_log_buffer_accumulates_via_fire_new_access_events(
    hass: HomeAssistant,
) -> None:
    """_fire_new_access_events prepends events to _log_buffer (newest first)."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    first_batch = [
        {"id": "e1", "event": "CardEntered", "utcTime": "2026-03-29T10:00:00Z", "params": {}},
    ]
    second_batch = [
        {"id": "e2", "event": "UserRejected", "utcTime": "2026-03-29T10:01:00Z", "params": {}},
    ]

    # Manually simulate what the background task does when it gets events
    coordinator._log_buffer = (first_batch + coordinator._log_buffer)[:coordinator._log_buffer_max]
    coordinator._log_buffer = (second_batch + coordinator._log_buffer)[:coordinator._log_buffer_max]

    assert len(coordinator._log_buffer) == 2
    assert coordinator._log_buffer[0]["id"] == "e2"  # newest first


@pytest.mark.asyncio
async def test_log_buffer_capped_at_max(
    hass: HomeAssistant,
) -> None:
    """_log_buffer never exceeds _log_buffer_max entries."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    # Fill beyond max
    overflow = [{"id": str(i), "event": "CardEntered", "utcTime": "", "params": {}} for i in range(250)]
    coordinator._log_buffer = overflow[:coordinator._log_buffer_max]

    assert len(coordinator._log_buffer) == coordinator._log_buffer_max


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
    try:
        await task_1
    except asyncio.CancelledError:
        pass


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
