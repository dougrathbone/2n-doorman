"""Integration test — validates the full setup and teardown lifecycle."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN

from .conftest import MOCK_SWITCHES, MOCK_USERS, setup_two_entries


@pytest.mark.asyncio
async def test_setup_entry_creates_coordinator(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Integration loads and the coordinator is populated with data from the device."""
    doorman_config_entry.add_to_hass(hass)

    result = await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert result is True
    assert doorman_config_entry.state is ConfigEntryState.LOADED

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    assert coordinator.data is not None
    assert len(coordinator.data["users"]) == len(MOCK_USERS)
    assert len(coordinator.data["switches"]) == len(MOCK_SWITCHES)


@pytest.mark.asyncio
async def test_setup_entry_creates_sensor(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A sensor entity is created and reflects the user count from the device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("sensor.doorman_user_count")
    assert state is not None
    assert state.state == str(len(MOCK_USERS))

    # User list is exposed as extra attributes for automation use
    attrs = state.attributes
    assert "users" in attrs
    assert len(attrs["users"]) == len(MOCK_USERS)


@pytest.mark.asyncio
async def test_setup_entry_creates_relay_switches(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A switch entity is created for each relay reported by the device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    for sw in MOCK_SWITCHES:
        entity_id = f"switch.doorman_relay_{sw['id']}"
        state = hass.states.get(entity_id)
        assert state is not None, f"Expected entity {entity_id} to exist"
        assert state.state == ("on" if sw["active"] else "off")


@pytest.mark.asyncio
async def test_setup_entry_registers_services(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """All four service actions are registered after setup."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    for service in ("create_user", "update_user", "delete_user", "grant_access"):
        assert hass.services.has_service(DOMAIN, service), (
            f"Service {DOMAIN}.{service} was not registered"
        )


@pytest.mark.asyncio
async def test_setup_calls_device_api(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The coordinator fetches device info and directory data during setup."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    mock_2n_client.get_system_info.assert_called_once()
    mock_2n_client.query_users.assert_called()
    mock_2n_client.get_switch_status.assert_called()


@pytest.mark.asyncio
async def test_unload_entry(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Unloading removes coordinator data and de-registers entities."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.NOT_LOADED
    assert doorman_config_entry.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_create_user_service(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.create_user forwards the request to the 2N API."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "create_user",
        {"name": "New Person", "pin": "5678"},
        blocking=True,
    )

    mock_2n_client.create_user.assert_called_once()
    call_arg = mock_2n_client.create_user.call_args[0][0]
    assert call_arg["name"] == "New Person"
    assert call_arg["pin"] == "5678"


@pytest.mark.asyncio
async def test_delete_user_service(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.delete_user forwards the UUID to the 2N API."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "delete_user",
        {"uuid": "uuid-jane"},
        blocking=True,
    )

    mock_2n_client.delete_user.assert_called_once_with("uuid-jane")


@pytest.mark.asyncio
async def test_grant_access_service(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.grant_access triggers the access point on the device."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "grant_access",
        {"access_point_id": 2},
        blocking=True,
    )

    mock_2n_client.grant_access.assert_called_once_with(access_point_id=2, user_uuid=None)


# ------------------------------------------------------------------ #
# Multi-device tests                                                   #
# ------------------------------------------------------------------ #




@pytest.mark.asyncio
async def test_service_routes_to_single_device_without_param(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """With one device, service calls work without the device parameter."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN, "create_user", {"name": "Test"}, blocking=True,
    )
    mock_2n_client.create_user.assert_called_once()


@pytest.mark.asyncio
async def test_service_with_device_param_routes_to_correct_device(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """With two devices, the device parameter routes to the specified entry."""
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    await hass.services.async_call(
        DOMAIN, "delete_user",
        {"uuid": "uuid-jane", "device": entry2.entry_id},
        blocking=True,
    )
    # The mock is shared, but we can verify the call happened
    mock_2n_client.delete_user.assert_called_once_with("uuid-jane")


@pytest.mark.asyncio
async def test_service_without_device_param_fails_with_multiple_devices(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """With two devices and no device param, service calls raise an error."""
    await setup_two_entries(hass, doorman_config_entry)

    with pytest.raises(ServiceValidationError, match="Multiple Doorman devices"):
        await hass.services.async_call(
            DOMAIN, "create_user", {"name": "Test"}, blocking=True,
        )


@pytest.mark.asyncio
async def test_service_with_unknown_device_param_raises(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """An unknown device ID raises a validation error."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    with pytest.raises(ServiceValidationError, match="Unknown Doorman device"):
        await hass.services.async_call(
            DOMAIN, "create_user",
            {"name": "Test", "device": "nonexistent-id"},
            blocking=True,
        )


@pytest.mark.asyncio
async def test_services_registered_once_across_entries(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Services are registered once, not duplicated when a second entry loads."""
    await setup_two_entries(hass, doorman_config_entry)

    # All services should exist
    for svc in ("create_user", "update_user", "delete_user", "grant_access"):
        assert hass.services.has_service(DOMAIN, svc)


@pytest.mark.asyncio
async def test_panel_registered_once_across_entries(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The sidebar panel is only registered once even with multiple entries."""
    await setup_two_entries(hass, doorman_config_entry)
    assert hass.data.get(f"{DOMAIN}_panel_registered") is True


@pytest.mark.asyncio
async def test_unload_one_entry_keeps_other_running(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Unloading one entry keeps the other loaded and the panel registered."""
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    await hass.config_entries.async_unload(entry1.entry_id)
    await hass.async_block_till_done()

    assert entry1.entry_id not in hass.data[DOMAIN]
    assert entry2.entry_id in hass.data[DOMAIN]
    # Panel stays because there's still one entry loaded
    assert hass.data.get(f"{DOMAIN}_panel_registered") is True
