"""Tests for the Doorman binary_sensor platform (door + hardware inputs)."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN


def _fire(hass: HomeAssistant, entry_id: str, event_type: str, params: dict) -> None:
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry_id,
            "event_type": event_type,
            "params": params,
            "utc_time": 1743242400,
        },
    )


@pytest.mark.asyncio
async def test_door_sensor_starts_unknown(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """No DoorStateChanged seen yet → state unknown (not on/off)."""
    state = hass.states.get("binary_sensor.doorman_door")
    assert state is not None
    assert state.state == "unknown"


@pytest.mark.asyncio
async def test_door_sensor_follows_door_state_events(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """DoorStateChanged opened/closed drives the door binary sensor."""
    _fire(hass, setup_doorman.entry_id, "DoorStateChanged", {"state": "opened"})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.doorman_door").state == "on"

    _fire(hass, setup_doorman.entry_id, "DoorStateChanged", {"state": "closed"})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.doorman_door").state == "off"


@pytest.mark.asyncio
async def test_door_sensor_ignores_other_entries(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """A DoorStateChanged from a different config entry must not move the sensor."""
    _fire(hass, "some-other-entry", "DoorStateChanged", {"state": "opened"})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.doorman_door").state == "unknown"


@pytest.mark.asyncio
async def test_input_sensors_created_from_io_caps(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """One binary sensor per input port; output ports are skipped."""
    state = hass.states.get("binary_sensor.doorman_input_input1")
    assert state is not None
    assert state.state == "off"  # MOCK_IO_STATUS: state 0
    # relay1 is an output — no sensor
    assert hass.states.get("binary_sensor.doorman_input_relay1") is None


@pytest.mark.asyncio
async def test_input_sensor_updates_from_event_via_coordinator(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """InputChanged flips the input sensor without waiting for a poll."""
    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    coordinator._fire_new_access_events(
        [{"event": "InputChanged", "utcTime": 1743242400, "params": {"port": "input1", "state": True}}]
    )
    # Simulate the listener's post-pull data push
    coordinator.async_set_updated_data(dict(coordinator.data))
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.doorman_input_input1").state == "on"


@pytest.mark.asyncio
async def test_switch_state_event_applies_immediately(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """SwitchStateChanged updates the relay entity without a poll cycle."""
    assert hass.states.get("switch.doorman_relay_1").state == "off"

    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    coordinator._fire_new_access_events(
        [{
            "event": "SwitchStateChanged",
            "utcTime": 1743242400,
            "params": {"switch": 1, "state": True, "originator": "auth"},
        }]
    )
    coordinator.async_set_updated_data(dict(coordinator.data))
    await hass.async_block_till_done()

    assert hass.states.get("switch.doorman_relay_1").state == "on"


@pytest.mark.asyncio
async def test_security_events_fire_on_bus(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Security events (UnauthorizedDoorOpen etc.) reach the doorman_access bus."""
    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    coordinator._fire_new_access_events([
        {"event": "UnauthorizedDoorOpen", "utcTime": 1743242400, "params": {"state": "in"}},
        {"event": "TamperSwitchActivated", "utcTime": 1743242460, "params": {"state": "in"}},
        {"event": "MotionDetected", "utcTime": 1743242500, "params": {"state": "in"}},
    ])
    await hass.async_block_till_done()

    types = [e.data["event_type"] for e in fired]
    assert "UnauthorizedDoorOpen" in types
    assert "TamperSwitchActivated" in types
    # MotionDetected is deliberately not surfaced (camera motion is not access control)
    assert "MotionDetected" not in types


@pytest.mark.asyncio
async def test_event_entity_maps_security_types(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """The event entity translates security events into HA event types."""
    _fire(hass, setup_doorman.entry_id, "UnauthorizedDoorOpen", {"state": "in"})
    await hass.async_block_till_done()

    state = hass.states.get("event.doorman_access")
    assert state.attributes.get("event_type") == "unauthorized_door_open"
    assert state.attributes.get("state") == "in"


@pytest.mark.asyncio
async def test_event_entity_skips_unmapped_types(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Unmapped event types never reach _trigger_event (which validates types)."""
    _fire(hass, setup_doorman.entry_id, "DoorStateChanged", {"state": "opened"})
    await hass.async_block_till_done()
    state = hass.states.get("event.doorman_access")
    assert state.attributes.get("event_type") == "door_state_changed"

    previous = state.state
    _fire(hass, setup_doorman.entry_id, "KeyPressed", {"key": "5"})
    await hass.async_block_till_done()
    assert hass.states.get("event.doorman_access").state == previous
