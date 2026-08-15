"""Tests for Doorman diagnostics."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN
from custom_components.doorman.diagnostics import async_get_config_entry_diagnostics

from .conftest import MOCK_DEVICE_INFO


@pytest.mark.asyncio
async def test_diagnostics_returns_coordinator_state(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Diagnostics includes device info, access points, and user/switch counts."""
    diag = await async_get_config_entry_diagnostics(hass, setup_doorman)

    assert diag["config_entry"]["host"] == "192.168.1.100"
    assert diag["config_entry"]["password"] == "**REDACTED**"
    coord = diag["coordinator"]
    assert coord["device_info"]["serialNumber"] == MOCK_DEVICE_INFO["serialNumber"]
    assert coord["has_write_permission"] is True
    assert coord["user_count"] == 2
    assert coord["switch_count"] == 1
    assert isinstance(coord["access_points"], list)
    assert coord["log_task_running"] is True


@pytest.mark.asyncio
async def test_diagnostics_includes_notification_settings(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Notification settings live in the store, so diagnostics must dump them."""
    store = hass.data[f"{DOMAIN}_store"]
    await store.set_notification_settings(setup_doorman.entry_id, {"doorbell_key_code": "%2"})

    diag = await async_get_config_entry_diagnostics(hass, setup_doorman)

    assert diag["notification_settings"]["doorbell_key_code"] == "%2"


@pytest.mark.asyncio
async def test_diagnostics_without_coordinator(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Diagnostics handles the case where the coordinator is not loaded."""
    doorman_config_entry.add_to_hass(hass)
    # Do not call async_setup — coordinator never registers
    diag = await async_get_config_entry_diagnostics(hass, doorman_config_entry)

    assert diag["coordinator"] == "not_loaded"
    assert diag["config_entry"]["password"] == "**REDACTED**"


@pytest.mark.asyncio
async def test_diagnostics_password_is_redacted(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Diagnostics never exposes the API password."""
    diag = await async_get_config_entry_diagnostics(hass, setup_doorman)

    # Ensure 'secret' (the fixture password) does not appear anywhere in the output
    diag_str = str(diag)
    assert "secret" not in diag_str
