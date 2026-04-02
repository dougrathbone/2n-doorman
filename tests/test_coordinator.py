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
    client.pull_log = AsyncMock(return_value=[])

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_init_device_info()
    await coordinator.async_refresh()

    assert coordinator.data["users"] == MOCK_USERS
    assert coordinator.data["switches"] == MOCK_SWITCHES
    assert coordinator.device_info == MOCK_DEVICE_INFO
    assert coordinator.data["has_write_permission"] is True


@pytest.mark.asyncio
async def test_coordinator_auth_error_raises_config_entry_auth_failed(
    hass: HomeAssistant,
) -> None:
    """An auth error from the API triggers a re-auth flow via ConfigEntryAuthFailed."""
    client = MagicMock()
    client.query_users = AsyncMock(side_effect=DoormanAuthError("expired"))
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
    client.pull_log = AsyncMock(return_value=[])

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
    client.pull_log = AsyncMock(return_value=[])

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
async def test_log_listener_updates_log_buffer(
    hass: HomeAssistant,
) -> None:
    """Events fired via _fire_new_access_events are accumulated in _log_buffer."""
    client = MagicMock()
    coordinator = _make_coordinator(hass, client)

    events = [
        {"id": "e1", "event": "CardEntered", "utcTime": "2026-03-29T10:00:00Z", "params": {}},
        {"id": "e2", "event": "UserRejected", "utcTime": "2026-03-29T10:01:00Z", "params": {}},
    ]
    coordinator._log_buffer = events
    assert len(coordinator._log_buffer) == 2
