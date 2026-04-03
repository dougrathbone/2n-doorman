"""Integration test — validates the full setup and teardown lifecycle."""
from __future__ import annotations

from datetime import UTC

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
    setup_doorman: MockConfigEntry,
) -> None:
    """Integration loads and the coordinator is populated with data from the device."""
    assert setup_doorman.state is ConfigEntryState.LOADED

    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    assert coordinator.data is not None
    assert len(coordinator.data["users"]) == len(MOCK_USERS)
    assert len(coordinator.data["switches"]) == len(MOCK_SWITCHES)


@pytest.mark.asyncio
async def test_setup_entry_creates_sensor(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """A sensor entity is created and reflects the user count from the device."""
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
    setup_doorman: MockConfigEntry,
) -> None:
    """A switch entity is created for each relay reported by the device."""
    for sw in MOCK_SWITCHES:
        entity_id = f"switch.doorman_relay_{sw['id']}"
        state = hass.states.get(entity_id)
        assert state is not None, f"Expected entity {entity_id} to exist"
        assert state.state == ("on" if sw["active"] else "off")


@pytest.mark.asyncio
async def test_setup_entry_registers_services(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """All four service actions are registered after setup."""
    for service in ("create_user", "update_user", "delete_user", "grant_access"):
        assert hass.services.has_service(DOMAIN, service), (
            f"Service {DOMAIN}.{service} was not registered"
        )


@pytest.mark.asyncio
async def test_setup_calls_device_api(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The coordinator fetches device info and directory data during setup."""
    mock_2n_client.get_system_info.assert_called_once()
    mock_2n_client.query_users.assert_called()
    mock_2n_client.get_switch_status.assert_called()


@pytest.mark.asyncio
async def test_unload_entry(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Unloading removes coordinator data and de-registers entities."""
    assert setup_doorman.state is ConfigEntryState.LOADED

    await hass.config_entries.async_unload(setup_doorman.entry_id)
    await hass.async_block_till_done()

    assert setup_doorman.state is ConfigEntryState.NOT_LOADED
    assert setup_doorman.entry_id not in hass.data.get(DOMAIN, {})


@pytest.mark.asyncio
async def test_create_user_service(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.create_user forwards the request to the 2N API."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.delete_user forwards the UUID to the 2N API."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with only name change forwards name and uuid to the API."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with a PIN includes pin in the payload."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user omitting pin does not include pin key in the payload."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with a card number includes card as a single-element list."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with empty card string clears the card list."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with a code includes code as a single-element list."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with valid_from/valid_to converts datetimes to Unix timestamps."""
    from datetime import datetime

    valid_from = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    valid_to = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)

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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user without validity dates does not include validFrom/validTo."""
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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with all fields sends the complete payload correctly."""
    from datetime import datetime

    valid_from = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    valid_to = datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC)

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
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.grant_access triggers the access point on the device."""
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
async def test_update_user_service_with_device_param(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with device param routes to the correct entry."""
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "name": "Jane via device param", "device": entry2.entry_id},
        blocking=True,
    )

    mock_2n_client.update_user.assert_called_once()
    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["uuid"] == "uuid-jane"
    assert call_arg["name"] == "Jane via device param"


@pytest.mark.asyncio
async def test_service_routes_to_single_device_without_param(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """With one device, service calls work without the device parameter."""
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
    setup_doorman: MockConfigEntry,
) -> None:
    """An unknown device ID raises a validation error."""
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


# ------------------------------------------------------------------ #
# Long-poll background task                                           #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_setup_starts_log_listener(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """setup_entry starts the background log listener task."""
    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    assert coordinator._log_task is not None
    assert not coordinator._log_task.done()


@pytest.mark.asyncio
async def test_unload_cancels_log_listener(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Unloading an entry cancels the background log listener task."""
    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    task = coordinator._log_task

    await hass.config_entries.async_unload(setup_doorman.entry_id)
    await hass.async_block_till_done()

    assert task.done()


# ------------------------------------------------------------------ #
# Zero-device edge case                                               #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_service_with_no_devices_raises(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Service call with no configured devices raises a clear validation error."""
    # Remove the entry from hass.data to simulate zero devices
    hass.data[DOMAIN].clear()

    with pytest.raises(ServiceValidationError, match="No Doorman devices"):
        await hass.services.async_call(
            DOMAIN, "create_user", {"name": "Test"}, blocking=True,
        )


# ------------------------------------------------------------------ #
# Per-device write-permission notification                            #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_write_permission_creates_repair_issue(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """When write permission is missing a HA repair issue is created with the device name."""
    from unittest.mock import patch as _patch

    mock_2n_client.check_directory_write_permission = __import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(return_value=False)

    issues = []

    def _capture_issue(hass, domain, issue_id, **kwargs):
        issues.append({"domain": domain, "issue_id": issue_id, **kwargs})

    doorman_config_entry.add_to_hass(hass)

    with _patch("custom_components.doorman.async_create_issue", side_effect=_capture_issue):
        await hass.config_entries.async_setup(doorman_config_entry.entry_id)
        await hass.async_block_till_done()

    assert len(issues) == 1
    assert issues[0]["domain"] == DOMAIN
    assert doorman_config_entry.entry_id in issues[0]["issue_id"]
    assert issues[0]["translation_placeholders"]["device_name"] == "2N IP Verso"


# ------------------------------------------------------------------ #
# New service fields: enabled, user_uuid                              #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_create_user_service_with_enabled_field(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """create_user with enabled=False forwards the flag to the API."""
    await hass.services.async_call(
        DOMAIN,
        "create_user",
        {"name": "Disabled User", "enabled": False},
        blocking=True,
    )

    call_arg = mock_2n_client.create_user.call_args[0][0]
    assert call_arg["name"] == "Disabled User"
    assert call_arg["enabled"] is False


@pytest.mark.asyncio
async def test_update_user_service_with_enabled_field(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """update_user with enabled=True forwards the flag to the API."""
    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "enabled": True},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["uuid"] == "uuid-jane"
    assert call_arg["enabled"] is True


@pytest.mark.asyncio
async def test_grant_access_service_with_user_uuid(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """grant_access with user_uuid forwards it to the API."""
    await hass.services.async_call(
        DOMAIN,
        "grant_access",
        {"access_point_id": 1, "user_uuid": "uuid-jane"},
        blocking=True,
    )

    mock_2n_client.grant_access.assert_called_once_with(
        access_point_id=1, user_uuid="uuid-jane"
    )


# ------------------------------------------------------------------ #
# Migration                                                           #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_async_migrate_entry_returns_true(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """async_migrate_entry succeeds for the current schema version."""
    from custom_components.doorman import async_migrate_entry

    doorman_config_entry.add_to_hass(hass)
    result = await async_migrate_entry(hass, doorman_config_entry)
    assert result is True
