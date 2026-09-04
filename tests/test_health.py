"""Tests for Doorman health entities (SIP registration, uptime, restart)."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.mark.asyncio
async def test_sip_registered_sensor_on(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """All registration-enabled accounts registered → sensor on."""
    state = hass.states.get("binary_sensor.doorman_1012345678_sip_registered")
    assert state is not None
    assert state.state == "on"
    assert state.attributes["accounts"][0]["registered"] is True


@pytest.mark.asyncio
async def test_sip_registered_sensor_off_when_unregistered(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A registration-enabled but unregistered account → sensor off."""
    mock_2n_client.get_phone_status.return_value = [
        {"account": 1, "enabled": True, "registrationEnabled": True, "registered": False}
    ]
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.doorman_1012345678_sip_registered").state == "off"


@pytest.mark.asyncio
async def test_no_sip_sensor_when_phone_status_unavailable(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Accounts without the Phone privilege get no SIP sensor (probe failed)."""
    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.get_phone_status.side_effect = DoormanApiError("code 2: invalid path")
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("binary_sensor.doorman_1012345678_sip_registered") is None


@pytest.mark.asyncio
async def test_uptime_sensor(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Uptime sensor reports boot time (systemTime - upTime) as a timestamp."""
    state = hass.states.get("sensor.doorman_1012345678_uptime")
    assert state is not None
    # MOCK_SYSTEM_STATUS: 1743242400 - 3600 = 1743238800
    assert state.state == datetime.fromtimestamp(1743238800, UTC).isoformat()


@pytest.mark.asyncio
async def test_restart_button_calls_device(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Pressing the restart button calls system/restart on the device."""
    await hass.services.async_call(
        "button", "press", {"entity_id": "button.doorman_1012345678_restart"}, blocking=True
    )
    mock_2n_client.restart_device.assert_called_once()


@pytest.mark.asyncio
async def test_restart_button_api_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A device error from the restart button surfaces as HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.restart_device.side_effect = DoormanApiError("code 10: no privilege")
    with pytest.raises(HomeAssistantError, match="Restart failed"):
        await hass.services.async_call(
            "button", "press", {"entity_id": "button.doorman_1012345678_restart"}, blocking=True
        )


@pytest.mark.asyncio
async def test_no_health_entities_when_system_status_unavailable(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """No uptime sensor or restart button when system/status is unsupported."""
    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.get_system_status.side_effect = DoormanApiError("code 2: invalid path")
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.doorman_1012345678_uptime") is None
    assert hass.states.get("button.doorman_1012345678_restart") is None
