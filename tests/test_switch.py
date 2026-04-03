"""Tests for the Doorman switch platform (relay entities)."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN


@pytest.mark.asyncio
async def test_turn_on_calls_set_switch(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """async_turn_on calls client.set_switch(id, 'on') and requests refresh."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_on",
        {"entity_id": "switch.doorman_relay_1"},
        blocking=True,
    )

    mock_2n_client.set_switch.assert_called_once_with(1, "on")


@pytest.mark.asyncio
async def test_turn_off_calls_set_switch(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """async_turn_off calls client.set_switch(id, 'off') and requests refresh."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.doorman_relay_1"},
        blocking=True,
    )

    mock_2n_client.set_switch.assert_called_once_with(1, "off")


@pytest.mark.asyncio
async def test_is_on_reflects_coordinator_data(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """is_on returns the correct state from coordinator data."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    # The fixture switch has active=False
    state = hass.states.get("switch.doorman_relay_1")
    assert state is not None
    assert state.state == "off"

    # Update coordinator data to mark the switch as active
    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    coordinator.data["switches"] = [{"id": 1, "name": "Main Door", "active": True}]
    coordinator.async_set_updated_data(coordinator.data)
    await hass.async_block_till_done()

    state = hass.states.get("switch.doorman_relay_1")
    assert state is not None
    assert state.state == "on"


@pytest.mark.asyncio
async def test_is_on_returns_false_when_data_is_none(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """is_on returns False when coordinator.data is None (defensive guard)."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    coordinator.data = None
    coordinator.async_set_updated_data(None)
    await hass.async_block_till_done()

    state = hass.states.get("switch.doorman_relay_1")
    assert state is not None
    # When data is None, is_on should return False -> state "off"
    assert state.state == "off"


@pytest.mark.asyncio
async def test_switch_has_extra_attributes(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Switch entity exposes device_name as an extra attribute."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("switch.doorman_relay_1")
    assert state is not None
    assert state.attributes.get("device_name") == "Main Door"
