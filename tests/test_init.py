"""Integration test — validates the full setup and teardown lifecycle."""
from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN

from .conftest import MOCK_SWITCHES, MOCK_USERS


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
async def test_update_user_service_name(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with only name change forwards name and uuid to the API."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "name": "Jane Updated"},
        blocking=True,
    )

    mock_2n_client.update_user.assert_called_once()
    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["uuid"] == "uuid-jane"
    assert call_arg["name"] == "Jane Updated"
    assert "pin" not in call_arg


@pytest.mark.asyncio
async def test_update_user_service_pin(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with a PIN includes pin in the payload."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "pin": "9999"},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["pin"] == "9999"


@pytest.mark.asyncio
async def test_update_user_service_empty_pin_not_forwarded(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user omitting pin does not include pin key in the payload."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "name": "Jane"},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert "pin" not in call_arg


@pytest.mark.asyncio
async def test_update_user_service_card(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with a card number includes card as a single-element list."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "card": "DEADBEEF"},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["card"] == ["DEADBEEF"]


@pytest.mark.asyncio
async def test_update_user_service_clear_card(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with empty card string clears the card list."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "card": ""},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["card"] == []


@pytest.mark.asyncio
async def test_update_user_service_code(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with a code includes code as a single-element list."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-john", "code": "1234"},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["code"] == ["1234"]


@pytest.mark.asyncio
async def test_update_user_service_valid_from_to(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with valid_from/valid_to converts datetimes to Unix timestamps."""
    from datetime import datetime, timezone

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    valid_from = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    valid_to = datetime(2027, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "valid_from": valid_from, "valid_to": valid_to},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["validFrom"] == int(valid_from.timestamp())
    assert call_arg["validTo"] == int(valid_to.timestamp())


@pytest.mark.asyncio
async def test_update_user_service_no_validity_dates(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user without validity dates does not include validFrom/validTo."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "name": "Jane"},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert "validFrom" not in call_arg
    assert "validTo" not in call_arg


@pytest.mark.asyncio
async def test_update_user_service_all_fields(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with all fields sends the complete payload correctly."""
    from datetime import datetime, timezone

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    valid_from = datetime(2026, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
    valid_to = datetime(2026, 12, 31, 23, 59, 59, tzinfo=timezone.utc)

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {
            "uuid": "uuid-jane",
            "name": "Jane Complete",
            "pin": "0000",
            "card": "CAFEBABE",
            "code": "5555",
            "valid_from": valid_from,
            "valid_to": valid_to,
        },
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["uuid"] == "uuid-jane"
    assert call_arg["name"] == "Jane Complete"
    assert call_arg["pin"] == "0000"
    assert call_arg["card"] == ["CAFEBABE"]
    assert call_arg["code"] == ["5555"]
    assert call_arg["validFrom"] == int(valid_from.timestamp())
    assert call_arg["validTo"] == int(valid_to.timestamp())


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
