"""Tests for Doorman WebSocket API handlers — multi-device routing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.websocket import (
    ws_get_access_log,
    ws_get_device_info,
    ws_list_devices,
    ws_list_users,
)

from .conftest import MOCK_DEVICE_INFO, setup_two_entries


def _mock_connection():
    conn = MagicMock()
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()
    return conn


@pytest.mark.asyncio
async def test_ws_list_devices_single(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_list_devices returns one device when a single entry is configured."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_list_devices(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    result = conn.send_result.call_args[0][1]
    devices = result["devices"]
    assert len(devices) == 1
    assert devices[0]["entry_id"] == doorman_config_entry.entry_id
    assert devices[0]["serial_number"] == MOCK_DEVICE_INFO["serialNumber"]
    assert devices[0]["device_name"] == MOCK_DEVICE_INFO["deviceName"]
    assert devices[0]["model"] == MOCK_DEVICE_INFO["hwVersion"]


@pytest.mark.asyncio
async def test_ws_list_devices_multiple(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_list_devices returns all configured devices."""
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    conn = _mock_connection()
    ws_list_devices(hass, conn, {"id": 1})

    result = conn.send_result.call_args[0][1]
    devices = result["devices"]
    assert len(devices) == 2
    entry_ids = {d["entry_id"] for d in devices}
    assert entry1.entry_id in entry_ids
    assert entry2.entry_id in entry_ids


@pytest.mark.asyncio
async def test_ws_list_users_with_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_list_users with entry_id routes to the specified device."""
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    conn = _mock_connection()
    ws_list_users(hass, conn, {"id": 1, "entry_id": entry2.entry_id})

    conn.send_result.assert_called_once()
    result = conn.send_result.call_args[0][1]
    assert "users" in result


@pytest.mark.asyncio
async def test_ws_list_users_without_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_list_users without entry_id returns users from the first device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_list_users(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    users = conn.send_result.call_args[0][1]["users"]
    assert len(users) == 2


@pytest.mark.asyncio
async def test_ws_list_users_invalid_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_list_users with a nonexistent entry_id returns not_configured error."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_list_users(hass, conn, {"id": 1, "entry_id": "nonexistent"})

    conn.send_error.assert_called_once()
    args = conn.send_error.call_args[0]
    assert args[1] == "not_configured"


@pytest.mark.asyncio
async def test_ws_get_device_info_with_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_device_info with entry_id returns info for the specified device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1, "entry_id": doorman_config_entry.entry_id})

    conn.send_result.assert_called_once()
    info = conn.send_result.call_args[0][1]["device_info"]
    assert info["serialNumber"] == MOCK_DEVICE_INFO["serialNumber"]


@pytest.mark.asyncio
async def test_ws_get_access_log_with_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_access_log with entry_id routes to the specified device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1, "entry_id": doorman_config_entry.entry_id})
    await hass.async_block_till_done()

    conn.send_result.assert_called_once()
    assert "events" in conn.send_result.call_args[0][1]


@pytest.mark.asyncio
async def test_ws_get_access_log_without_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_access_log without entry_id falls back to the first device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    assert "events" in conn.send_result.call_args[0][1]


@pytest.mark.asyncio
async def test_ws_get_access_log_invalid_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_access_log with an unknown entry_id returns not_configured."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1, "entry_id": "nonexistent"})

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "not_configured"


@pytest.mark.asyncio
async def test_ws_get_device_info_includes_access_points(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_device_info response includes the access_points list."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1, "entry_id": doorman_config_entry.entry_id})

    conn.send_result.assert_called_once()
    result = conn.send_result.call_args[0][1]
    assert "access_points" in result
    assert isinstance(result["access_points"], list)
    assert len(result["access_points"]) >= 1
    assert result["access_points"][0]["id"] == 1
    assert result["access_points"][0]["name"] == "Access point 1"


@pytest.mark.asyncio
async def test_ws_get_device_info_without_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_device_info without entry_id falls back to the first device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    info = conn.send_result.call_args[0][1]["device_info"]
    assert info["serialNumber"] == MOCK_DEVICE_INFO["serialNumber"]


@pytest.mark.asyncio
async def test_ws_get_device_info_invalid_entry_id(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_device_info with an unknown entry_id returns not_configured."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1, "entry_id": "nonexistent"})

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "not_configured"


@pytest.mark.asyncio
async def test_ws_get_access_log_multi_device_routing(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """ws_get_access_log with entry_id correctly targets one of two devices."""
    from .conftest import setup_two_entries

    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    # Seed different log data so we can tell them apart
    from custom_components.doorman.const import DOMAIN
    coord1 = hass.data[DOMAIN][entry1.entry_id]
    coord2 = hass.data[DOMAIN][entry2.entry_id]
    coord1.data = {**coord1.data, "log_events": [{"event": "device-1-event"}]}
    coord2.data = {**coord2.data, "log_events": [{"event": "device-2-event"}]}

    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1, "entry_id": entry2.entry_id})

    events = conn.send_result.call_args[0][1]["events"]
    assert events[0]["event"] == "device-2-event"
