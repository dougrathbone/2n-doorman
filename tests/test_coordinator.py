"""Tests for the Doorman coordinator — polling, error handling, and event firing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.api_client import DoormanAuthError, DoormanApiError
from custom_components.doorman.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.doorman.coordinator import DoormanCoordinator

from .conftest import MOCK_DEVICE_INFO, MOCK_LOG_EVENTS, MOCK_SWITCHES, MOCK_USERS


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
    client.query_users = AsyncMock(return_value=MOCK_USERS)
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
    client.pull_log = AsyncMock(return_value=[])

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_init_device_info()
    await coordinator.async_refresh()

    assert coordinator.data["users"] == MOCK_USERS
    assert coordinator.data["switches"] == MOCK_SWITCHES
    assert coordinator.device_info == MOCK_DEVICE_INFO


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
    """New access log entries are fired as doorman_access events on the HA bus."""
    client = MagicMock()
    client.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)
    client.query_users = AsyncMock(return_value=MOCK_USERS)
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
    # First poll: one event (establishes baseline, no fire)
    client.pull_log = AsyncMock(return_value=MOCK_LOG_EVENTS)

    coordinator = _make_coordinator(hass, client)
    await coordinator.async_refresh()

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    # Second poll: new event arrives
    new_event = {
        "id": "evt-002",
        "event": "CardEntered",
        "utcTime": "2026-03-29T10:05:00Z",
        "params": {"card": "AABBCCDD", "valid": True},
    }
    client.pull_log = AsyncMock(return_value=[*MOCK_LOG_EVENTS, new_event])
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(fired_events) == 1
    assert fired_events[0].data["event_type"] == "CardEntered"


@pytest.mark.asyncio
async def test_coordinator_no_duplicate_events_on_repeat_poll(
    hass: HomeAssistant,
) -> None:
    """Polling the same log events twice does not fire duplicate bus events."""
    client = MagicMock()
    client.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)
    client.query_users = AsyncMock(return_value=MOCK_USERS)
    client.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
    client.pull_log = AsyncMock(return_value=MOCK_LOG_EVENTS)

    coordinator = _make_coordinator(hass, client)

    fired_events = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired_events.append(e))

    # Poll three times with the same log data
    for _ in range(3):
        await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert len(fired_events) == 0
