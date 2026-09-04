"""Integration test — validates the full setup and teardown lifecycle."""
from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC
from unittest.mock import AsyncMock, MagicMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from pytest_homeassistant_custom_component.common import MockConfigEntry, MockUser

from custom_components.doorman.const import DOMAIN, LOG_STORAGE_KEY, LOG_STORAGE_VERSION

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
    state = hass.states.get("sensor.doorman_1012345678_user_count")
    assert state is not None
    assert state.state == str(len(MOCK_USERS))
    assert "users" not in state.attributes


@pytest.mark.asyncio
async def test_setup_entry_creates_relay_switches(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """A switch entity is created for each relay reported by the device."""
    for sw in MOCK_SWITCHES:
        entity_id = f"switch.doorman_1012345678_relay_{sw['id']}"
        state = hass.states.get(entity_id)
        assert state is not None, f"Expected entity {entity_id} to exist"
        assert state.state == ("on" if sw["active"] else "off")


@pytest.mark.asyncio
async def test_setup_entry_registers_services(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """All six service actions are registered after setup."""
    for service in (
        "create_user",
        "update_user",
        "delete_user",
        "grant_access",
        "hangup_calls",
        "resync_log_history",
    ):
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
async def test_create_user_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
    hass_read_only_user: MockUser,
) -> None:
    """doorman.create_user is admin-gated — non-admin callers are rejected."""
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "create_user",
            {"name": "Nope"},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    mock_2n_client.create_user.assert_not_called()


@pytest.mark.asyncio
async def test_create_user_rejects_invalid_pin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """create_user pin must be 2–15 digits (or omitted)."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN,
            "create_user",
            {"name": "Bad Pin", "pin": "1"},
            blocking=True,
        )
    mock_2n_client.create_user.assert_not_called()


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


@pytest.mark.asyncio
async def test_grant_access_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
    hass_read_only_user: MockUser,
) -> None:
    """doorman.grant_access is admin-gated — non-admin callers are rejected."""
    with pytest.raises(Unauthorized):
        await hass.services.async_call(
            DOMAIN,
            "grant_access",
            {"access_point_id": 1},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )
    mock_2n_client.grant_access.assert_not_called()


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


@pytest.mark.asyncio
async def test_store_created_once_across_entries(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The shared store is instantiated once, not recreated per config entry."""
    from unittest.mock import patch as _patch

    from custom_components.doorman import DoormanStore

    with _patch(
        "custom_components.doorman.DoormanStore", wraps=DoormanStore
    ) as spy:
        await setup_two_entries(hass, doorman_config_entry)

    assert spy.call_count == 1


@pytest.mark.asyncio
async def test_setup_retries_when_device_unreachable(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A device error during init raises ConfigEntryNotReady so HA retries setup."""
    from custom_components.doorman.api_client import DoormanConnectionError

    mock_2n_client.get_system_info.side_effect = DoormanConnectionError("offline")
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.SETUP_RETRY


# ------------------------------------------------------------------ #
# Setup / reload robustness                                            #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_unload_and_reload_entry_succeeds(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """setup → unload → setup again works, and the panel is untouched throughout."""
    await hass.config_entries.async_unload(setup_doorman.entry_id)
    await hass.async_block_till_done()
    assert setup_doorman.state is ConfigEntryState.NOT_LOADED

    await hass.config_entries.async_setup(setup_doorman.entry_id)
    await hass.async_block_till_done()

    assert setup_doorman.state is ConfigEntryState.LOADED
    assert hass.data.get(f"{DOMAIN}_panel_registered") is True


@pytest.mark.asyncio
async def test_static_path_and_panel_are_registered_once_per_run(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The frontend route and panel are registered in async_setup, once.

    Replaces test_setup_tolerates_duplicate_static_path_registration, which
    forced ``async_register_static_paths`` to raise ``ValueError("Duplicate")``
    and asserted setup survived it. aiohttp does not behave that way: on 3.13.3
    (shipped with current HA) ``HomeAssistantHTTP._async_register_static_paths``
    has no dedupe and static resources are unnamed, so a repeat registration
    silently appends a second router resource. The old ``suppress(ValueError)``
    was dead code guarding an exception that is never raised. Registration now
    happens once per HA run, so re-registration cannot happen at all — which is
    what this asserts instead.
    """
    from unittest.mock import patch

    register_panel = AsyncMock()
    with patch(
        "custom_components.doorman.panel_custom.async_register_panel", new=register_panel
    ):
        entry1, _entry2 = await setup_two_entries(hass, doorman_config_entry)
        await hass.config_entries.async_reload(entry1.entry_id)
        await hass.async_block_till_done()

    assert hass.http.async_register_static_paths.await_count == 1
    register_panel.assert_called_once()
    assert hass.data.get(f"{DOMAIN}_panel_registered") is True


# ------------------------------------------------------------------ #
# Domain-level registration is independent of the device being up      #
# ------------------------------------------------------------------ #


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_unreachable_intercom_still_gets_a_panel_and_services(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
    hass_ws_client,
) -> None:
    """An intercom that is offline at startup must not cost the user the panel.

    async_setup_entry raises ConfigEntryNotReady when the device cannot be
    reached and HA retries with backoff, indefinitely. Everything registered
    after that point used to be unreachable for as long as the device stayed
    down, so a restart with the intercom unplugged produced no Doorman sidebar
    entry at all — the "I restarted and it still didn't work" report. The panel
    is meant to be what tells the user the device is unreachable.
    """
    from unittest.mock import patch

    from custom_components.doorman.api_client import DoormanConnectionError

    mock_2n_client.get_system_info.side_effect = DoormanConnectionError("offline")
    register_panel = AsyncMock()

    doorman_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.doorman.panel_custom.async_register_panel", new=register_panel
    ):
        await hass.config_entries.async_setup(doorman_config_entry.entry_id)
        await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.SETUP_RETRY
    assert hass.data[DOMAIN] == {}

    # The sidebar panel and its assets are registered anyway…
    register_panel.assert_called_once()
    assert register_panel.call_args.kwargs["frontend_url_path"] == DOMAIN
    # (this test runs against the real http component, so the static route was
    # registered for real — the once-per-run assertion is in
    # test_static_path_and_panel_are_registered_once_per_run)
    # …as is the shared store several WS handlers and notifications.py read.
    assert hass.data.get(f"{DOMAIN}_store") is not None
    # …and every service action.
    for service in (
        "create_user",
        "update_user",
        "delete_user",
        "grant_access",
        "hangup_calls",
        "resync_log_history",
    ):
        assert hass.services.has_service(DOMAIN, service)

    # The WS commands answer, and answer sanely with zero loaded entries.
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "doorman/list_devices"})
    res = await client.receive_json()
    assert res["success"]
    assert res["result"] == {"devices": []}

    for command in (
        "doorman/list_users",
        "doorman/get_device_info",
        "doorman/get_access_log",
        "doorman/get_notification_settings",
    ):
        await client.send_json_auto_id({"type": command})
        res = await client.receive_json()
        assert not res["success"], command
        assert res["error"]["code"] == "not_configured", command

    # Store-only commands work without any device configured.
    ha_user = await hass.auth.async_create_user("Offline Link User")
    await client.send_json_auto_id(
        {
            "type": "doorman/link_user",
            "two_n_uuid": "uuid-jane",
            "ha_user_id": ha_user.id,
        }
    )
    assert (await client.receive_json())["success"]

    # And a service call explains itself instead of the service being missing.
    with pytest.raises(ServiceValidationError, match="No Doorman devices"):
        await hass.services.async_call(
            DOMAIN, "create_user", {"name": "Test"}, blocking=True
        )


@pytest.mark.asyncio
async def test_panel_registration_failure_does_not_stop_entry_load(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A broken sidebar panel must not prevent entities and services from loading.

    ``async_setup`` catches panel registration failures so a missing sidebar
    entry is never the reason the rest of the integration stops working — the
    same failure mode as the unreachable-intercom case, just in the other
    direction.
    """
    from unittest.mock import patch

    doorman_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.doorman._async_register_panel",
        side_effect=RuntimeError("panel registration failed"),
    ):
        assert await hass.config_entries.async_setup(doorman_config_entry.entry_id)
        await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.LOADED
    assert hass.data.get(f"{DOMAIN}_panel_registered") is not True

    for service in (
        "create_user",
        "update_user",
        "delete_user",
        "grant_access",
        "hangup_calls",
        "resync_log_history",
    ):
        assert hass.services.has_service(DOMAIN, service)

    assert hass.states.get("sensor.doorman_1012345678_user_count") is not None
    assert hass.states.get("switch.doorman_1012345678_relay_1") is not None


@pytest.mark.asyncio
async def test_reloading_the_only_entry_does_not_remove_the_panel(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """A reload must not take the sidebar entry away and put it back.

    Reloading the only entry went through the last-entry teardown, which called
    frontend.async_remove_panel() and then re-registered — flickering the
    sidebar and briefly removing every doorman.* service. Options changes and
    reauth both reload, so this was the common case, not a corner one.

    The teardown only shows up *inside* the reload window, so the panel flag
    and a service are sampled at the moment the entry is set up again — after
    the unload half of the reload has finished. Asserting afterwards would see
    everything freshly re-registered and pass either way.
    """
    from unittest.mock import patch

    import custom_components.doorman as module

    real_setup = module.async_setup_entry
    samples: list[dict] = []

    async def sampling_setup(hass, entry):
        samples.append({
            "panel": hass.data.get(f"{DOMAIN}_panel_registered"),
            "service": hass.services.has_service(DOMAIN, "create_user"),
            "store": hass.data.get(f"{DOMAIN}_store") is not None,
        })
        return await real_setup(hass, entry)

    with (
        patch.object(module, "async_setup_entry", sampling_setup),
        patch("homeassistant.components.frontend.async_remove_panel") as remove_panel,
    ):
        await hass.config_entries.async_reload(setup_doorman.entry_id)
        await hass.async_block_till_done()

    remove_panel.assert_not_called()
    assert samples == [{"panel": True, "service": True, "store": True}]
    assert setup_doorman.state is ConfigEntryState.LOADED


@pytest.mark.asyncio
async def test_failed_platform_setup_leaves_no_phantom_coordinator(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A platform failure must not leave the coordinator behind in hass.data.

    HA marks the entry SETUP_ERROR and never calls async_unload_entry for an
    entry that did not finish setting up, so a coordinator left in hass.data
    would be resolved by WS commands and services as a phantom device — and
    would keep hass.data[DOMAIN] non-empty forever.
    """
    from unittest.mock import patch

    doorman_config_entry.add_to_hass(hass)
    with patch.object(
        hass.config_entries,
        "async_forward_entry_setups",
        side_effect=RuntimeError("platform boom"),
    ):
        await hass.config_entries.async_setup(doorman_config_entry.entry_id)
        await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert hass.data[DOMAIN] == {}
    with pytest.raises(ServiceValidationError, match="No Doorman devices"):
        await hass.services.async_call(
            DOMAIN, "create_user", {"name": "Test"}, blocking=True
        )


@pytest.mark.asyncio
async def test_setup_auth_error_starts_reauth(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Rejected credentials at setup fail the entry and start the reauth flow."""
    from custom_components.doorman.api_client import DoormanAuthError

    mock_2n_client.get_system_info.side_effect = DoormanAuthError("bad creds")
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        flow["context"]["source"] == "reauth"
        for flow in hass.config_entries.flow.async_progress()
    )


@pytest.mark.asyncio
async def test_setup_timeout_retries(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A bare timeout during init is transient — HA retries setup later."""
    mock_2n_client.get_system_info.side_effect = TimeoutError("slow")
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.asyncio
async def test_panel_module_url_is_cache_busted(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """panel.js is registered with a ?v=<version> suffix for cache busting."""
    from unittest.mock import AsyncMock, patch

    register_panel = AsyncMock()
    doorman_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.doorman.panel_custom.async_register_panel", new=register_panel
    ):
        await hass.config_entries.async_setup(doorman_config_entry.entry_id)
        await hass.async_block_till_done()

    register_panel.assert_called_once()
    module_url = register_panel.call_args.kwargs["module_url"]
    assert module_url.startswith("/api/doorman/panel.js?v=")
    version = module_url.removeprefix("/api/doorman/panel.js?v=")
    assert len(version) > 0
    # The same version reaches the panel element as panel.config.version, which
    # panel.js compares against its own PANEL_VERSION to detect a browser tab
    # still running pre-update frontend code.
    assert register_panel.call_args.kwargs["config"] == {"version": version}


@pytest.mark.asyncio
async def test_panel_js_version_matches_the_manifest() -> None:
    """panel.js's PANEL_VERSION must track manifest.json.

    The backend passes its version to the panel and panel.js shows a "reload
    this page" banner when it differs from PANEL_VERSION. A PANEL_VERSION left
    behind at release time would show that banner to everyone, permanently.
    """
    import json
    import re
    from pathlib import Path

    root = Path(__file__).parent.parent / "custom_components" / "doorman"
    manifest = json.loads((root / "manifest.json").read_text())
    source = (root / "frontend" / "panel.js").read_text()

    match = re.search(r'^const PANEL_VERSION = "([^"]+)";', source, re.MULTILINE)
    assert match, "PANEL_VERSION constant not found in panel.js"
    assert match.group(1) == manifest["version"]


@pytest.mark.asyncio
async def test_panel_js_guards_every_custom_element_definition() -> None:
    """Every customElements.define() must be guarded against a re-import.

    panel.js is served with a ?v=<version> cache-buster and HA re-imports module
    panels per URL, so after a HACS update the module re-executes in a document
    that already has the previous version's elements defined. An unguarded
    define() throws NotSupportedError and the whole panel fails to render.
    """
    from pathlib import Path

    source = (
        Path(__file__).parent.parent
        / "custom_components" / "doorman" / "frontend" / "panel.js"
    ).read_text()

    # The only permitted call site is the guarded define() helper.
    calls = [
        line for line in source.splitlines()
        if "customElements.define(" in line and not line.lstrip().startswith(("//", "*"))
    ]
    assert calls == ["  if (!customElements.get(name)) customElements.define(name, cls);"], (
        f"Unguarded customElements.define() call(s): {calls}"
    )


@pytest.mark.asyncio
async def test_panel_js_empty_state_and_tablist_a11y() -> None:
    """Static markers for zero-device empty state and tab accessibility."""
    from pathlib import Path

    source = (
        Path(__file__).parent.parent
        / "custom_components" / "doorman" / "frontend" / "panel.js"
    ).read_text()

    assert 'role="tablist"' in source
    assert "No devices configured" in source
    assert "Resync history" in source
    assert 'aria-label="Menu"' in source


# ------------------------------------------------------------------ #
# Service error handling & lifecycle                                   #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_create_user_service_api_error_raises_ha_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A 2N API failure during create_user surfaces as HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.create_user.side_effect = DoormanApiError("device busy")

    with pytest.raises(HomeAssistantError, match="create_user failed on the 2N device"):
        await hass.services.async_call(
            DOMAIN, "create_user", {"name": "Test"}, blocking=True,
        )


@pytest.mark.asyncio
async def test_update_user_service_api_error_raises_ha_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A 2N API failure during update_user surfaces as HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.update_user.side_effect = DoormanApiError("device busy")

    with pytest.raises(HomeAssistantError, match="update_user failed on the 2N device"):
        await hass.services.async_call(
            DOMAIN, "update_user", {"uuid": "uuid-jane", "name": "X"}, blocking=True,
        )


@pytest.mark.asyncio
async def test_delete_user_service_api_error_raises_ha_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A 2N API failure during delete_user surfaces as HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.delete_user.side_effect = DoormanApiError("device busy")

    with pytest.raises(HomeAssistantError, match="delete_user failed on the 2N device"):
        await hass.services.async_call(
            DOMAIN, "delete_user", {"uuid": "uuid-jane"}, blocking=True,
        )


@pytest.mark.asyncio
async def test_grant_access_service_api_error_raises_ha_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A 2N API failure during grant_access surfaces as HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.grant_access.side_effect = DoormanApiError("device busy")

    with pytest.raises(HomeAssistantError, match="grant_access failed on the 2N device"):
        await hass.services.async_call(
            DOMAIN, "grant_access", {"access_point_id": 1}, blocking=True,
        )


@pytest.mark.asyncio
async def test_services_and_panel_survive_the_last_entry_unloading(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Unloading the sole entry keeps the domain-level registrations in place.

    Replaces test_services_removed_when_last_entry_unloads. The services, the
    panel and the shared store are registered in async_setup — once per HA run
    — and are no longer torn down per entry: an ordinary reload of a
    single-entry install went through this path, so removing them made the
    sidebar entry flicker away and every doorman.* service disappear mid-reload
    (and, if the device was unreachable on the way back up, never return).
    A service call with nothing loaded raises a clean validation error instead,
    which is asserted by test_service_with_no_devices_raises.
    """
    await hass.config_entries.async_unload(setup_doorman.entry_id)
    await hass.async_block_till_done()

    assert hass.data[DOMAIN] == {}
    for service in (
        "create_user",
        "update_user",
        "delete_user",
        "grant_access",
        "hangup_calls",
        "resync_log_history",
    ):
        assert hass.services.has_service(DOMAIN, service), (
            f"Service {DOMAIN}.{service} should have survived the unload"
        )
    assert hass.data.get(f"{DOMAIN}_panel_registered") is True
    assert hass.data.get(f"{DOMAIN}_store") is not None


@pytest.mark.asyncio
async def test_delete_user_clears_notification_targets(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """delete_user also drops the deleted user's stored notification targets."""
    store = hass.data[f"{DOMAIN}_store"]
    await store.set_notification_targets("uuid-jane", ["notify.mobile_app"])
    assert store.get_notification_targets("uuid-jane") == ["notify.mobile_app"]

    await hass.services.async_call(
        DOMAIN, "delete_user", {"uuid": "uuid-jane"}, blocking=True,
    )

    mock_2n_client.delete_user.assert_called_once_with("uuid-jane")
    assert store.get_notification_targets("uuid-jane") == []


# ------------------------------------------------------------------ #
# update_user: clearing fields                                         #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_update_user_service_clear_validity(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """valid_from/valid_to of 0 are passed through to clear the restriction."""
    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "valid_from": 0, "valid_to": 0},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["validFrom"] == 0
    assert call_arg["validTo"] == 0


@pytest.mark.asyncio
async def test_update_user_service_clear_pin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """An explicitly empty pin string is forwarded to clear the PIN."""
    await hass.services.async_call(
        DOMAIN,
        "update_user",
        {"uuid": "uuid-jane", "pin": ""},
        blocking=True,
    )

    call_arg = mock_2n_client.update_user.call_args[0][0]
    assert call_arg["pin"] == ""


# ------------------------------------------------------------------ #
# Device registry                                                      #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_entities_attached_to_device_registry(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """All Doorman entities attach to a single device per config entry."""
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, setup_doorman.entry_id)}
    )
    assert device is not None
    assert device.manufacturer == "2N"
    assert device.name == setup_doorman.title
    assert device.model == "535v1"
    assert device.hw_version == "535v1"
    assert device.sw_version == "2.49.0.38"
    assert device.serial_number == "10-12345678"
    assert device.configuration_url == "https://192.168.1.100/"

    entity_registry = er.async_get(hass)
    for entity_id in (
        "sensor.doorman_1012345678_user_count",
        "switch.doorman_1012345678_relay_1",
        "event.doorman_1012345678_access",
    ):
        entity = entity_registry.async_get(entity_id)
        assert entity is not None, f"Missing entity {entity_id}"
        assert entity.device_id == device.id
        assert entity.has_entity_name is False

@pytest.mark.asyncio
async def test_hangup_calls_service(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Calling doorman.hangup_calls hangs up all active sessions on the device."""
    mock_2n_client.hangup_all_calls.return_value = 2

    await hass.services.async_call(DOMAIN, "hangup_calls", {}, blocking=True)

    mock_2n_client.hangup_all_calls.assert_called_once()


@pytest.mark.asyncio
async def test_hangup_calls_service_api_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A device error from hangup_calls surfaces as HomeAssistantError, not a traceback."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.hangup_all_calls.side_effect = DoormanApiError("code 10: no privilege")

    with pytest.raises(HomeAssistantError, match="hangup_calls failed"):
        await hass.services.async_call(DOMAIN, "hangup_calls", {}, blocking=True)


# ─── Persistent access log ───────────────────────────────────────────────────


def _stored_log(entry_id: str, events: list[dict]) -> tuple[str, dict]:
    """Return the (storage key, payload) pair for a pre-existing access log."""
    key = f"{LOG_STORAGE_KEY}.{entry_id}"
    return key, {"version": LOG_STORAGE_VERSION, "minor_version": 1, "key": key,
                 "data": {"events": events}}


@pytest.mark.asyncio
async def test_access_log_from_previous_run_is_available_to_the_panel(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
    hass_storage,
) -> None:
    """Events stored before a restart are served to the panel immediately."""
    from custom_components.doorman.websocket import ws_get_access_log

    old_event = {
        "id": 1,
        "event": "UserAuthenticated",
        "utcTime": 1743000000,
        "params": {"name": "Jane", "uuid": "uuid-jane"},
    }
    key, payload = _stored_log(doorman_config_entry.entry_id, [old_event])
    hass_storage[key] = payload

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    conn = MagicMock()
    conn.user = MagicMock(is_admin=True)
    ws_get_access_log(hass, conn, {"id": 1, "entry_id": doorman_config_entry.entry_id})

    events = conn.send_result.call_args[0][1]["events"]
    assert 1 in [e["id"] for e in events]


@pytest.mark.asyncio
async def test_setup_backfills_history_without_notifying(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """On-box history is merged in at setup but fires no doorman_access events."""
    historical = [
        {
            "id": 5,
            "event": "UserAuthenticated",
            "utcTime": 1743100000,
            "params": {"name": "Jane", "uuid": "uuid-jane"},
        },
    ]
    mock_2n_client.fetch_log_history = AsyncMock(return_value=historical)

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    stored_ids = [e["id"] for e in coordinator.log_store.events]
    assert 5 in stored_ids
    # The live listener still delivered (and fired for) the fixture's event
    assert [e.data["params"].get("uuid") for e in fired] == ["uuid-jane"]
    assert len(fired) == 1
    assert "evt-001" in stored_ids


@pytest.mark.asyncio
async def test_setup_survives_a_device_without_history_support(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A device that cannot serve history still sets up and logs live events."""
    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.fetch_log_history = AsyncMock(
        side_effect=DoormanApiError("API error 18: invalid parameter", code=18)
    )

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    assert [e["id"] for e in coordinator.log_store.events] == ["evt-001"]


@pytest.mark.asyncio
async def test_removing_an_entry_deletes_its_access_log(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_storage,
) -> None:
    """The per-entry log file is cleaned up when the device is removed."""
    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    await coordinator.log_store.async_flush()
    key = f"{LOG_STORAGE_KEY}.{setup_doorman.entry_id}"
    assert key in hass_storage

    await hass.config_entries.async_remove(setup_doorman.entry_id)
    await hass.async_block_till_done()

    assert key not in hass_storage


@pytest.mark.asyncio
async def test_removing_an_entry_clears_doorman_store(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Remove drops this entry's notification settings and its users' UUID maps."""
    store = hass.data[f"{DOMAIN}_store"]
    entry_id = setup_doorman.entry_id

    await store.set_notification_settings(entry_id, {"doorbell_key_code": "%2"})
    await store.set_notification_settings("other-entry", {"doorbell_key_code": "%3"})
    await store.link_user("uuid-jane", "ha-1")
    await store.link_user("uuid-stranger", "ha-2")
    await store.set_notification_targets("uuid-jane", ["notify.phone"])
    await store.set_notification_targets("uuid-stranger", ["notify.tablet"])
    await store.update_last_access("uuid-jane", 1743242400)
    await store.update_last_access("uuid-stranger", 1743242500)

    await hass.config_entries.async_remove(entry_id)
    await hass.async_block_till_done()

    assert entry_id not in store.notification_settings
    assert store.get_notification_settings("other-entry")["doorbell_key_code"] == "%3"
    # MOCK_USERS are uuid-jane and uuid-john — stranger belongs to another device.
    assert store.get_ha_user_id("uuid-jane") is None
    assert store.get_ha_user_id("uuid-john") is None
    assert store.get_ha_user_id("uuid-stranger") == "ha-2"
    assert store.get_notification_targets("uuid-jane") == []
    assert store.get_notification_targets("uuid-stranger") == ["notify.tablet"]
    assert "uuid-jane" not in store.last_access
    assert store.last_access["uuid-stranger"] == 1743242500


@pytest.mark.asyncio
async def test_setup_seeds_last_access_only_for_this_device(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """_last_access is restored only for UUIDs present on this device."""
    store = hass.data.get(f"{DOMAIN}_store")
    if store is None:
        from custom_components.doorman.storage import DoormanStore

        store = DoormanStore(hass)
        await store.async_load()
        hass.data[f"{DOMAIN}_store"] = store

    await store.update_last_access("uuid-jane", 1743242400)
    await store.update_last_access("uuid-other-device", 1743249999)

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    assert coordinator._last_access == {"uuid-jane": 1743242400}


# ─── Backfill runs off the setup path ────────────────────────────────────────


_HISTORY_EVENT = {
    "id": 42,
    "event": "UserAuthenticated",
    "utcTime": 1743500000,
    "params": {"name": "Jane", "uuid": "uuid-jane"},
}


def _gated_history(release: asyncio.Event, started: asyncio.Event, events: list[dict]):
    """Return a fetch_log_history stand-in that hangs until ``release`` is set.

    It gives up after a few seconds so that a regression (backfill awaited on
    the setup path again) fails the assertions below instead of wedging the
    test run — ``hass.config_entries.async_setup`` cannot be cancelled from the
    outside, so an outer timeout would not rescue us.
    """

    async def _fetch(**_kwargs) -> list[dict]:
        started.set()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(release.wait(), 5)
        return events

    return _fetch


def _stored_ids(hass: HomeAssistant, entry_id: str) -> list:
    return [e["id"] for e in hass.data[DOMAIN][entry_id].log_store.events]


@pytest.mark.asyncio
async def test_setup_does_not_wait_for_backfill(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A device that never answers the history request must not stall setup."""
    release, started = asyncio.Event(), asyncio.Event()
    mock_2n_client.fetch_log_history = _gated_history(release, started, [_HISTORY_EVENT])

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert doorman_config_entry.state is ConfigEntryState.LOADED
    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    # Backfill was started, is still in flight, and setup did not wait for it.
    assert started.is_set()
    assert not coordinator._backfill_task.done()
    assert 42 not in _stored_ids(hass, doorman_config_entry.entry_id)

    release.set()
    await coordinator._backfill_task


@pytest.mark.asyncio
async def test_backfill_populates_the_store_after_setup(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The history still lands in the store — just later than setup."""
    release, started = asyncio.Event(), asyncio.Event()
    mock_2n_client.fetch_log_history = _gated_history(release, started, [_HISTORY_EVENT])

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    release.set()
    await coordinator._backfill_task
    await hass.async_block_till_done()

    assert 42 in _stored_ids(hass, doorman_config_entry.entry_id)
    # Only the live listener's event notified; the backfilled one never did.
    assert [e.data["params"].get("uuid") for e in fired] == ["uuid-jane"]
    assert len(fired) == 1


@pytest.mark.asyncio
async def test_unload_mid_backfill_cancels_cleanly(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
    hass_storage,
    caplog,
) -> None:
    """Removing the entry while backfill is in flight leaves nothing behind."""
    release, started = asyncio.Event(), asyncio.Event()
    mock_2n_client.fetch_log_history = _gated_history(release, started, [_HISTORY_EVENT])

    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    task = coordinator._backfill_task
    key = f"{LOG_STORAGE_KEY}.{doorman_config_entry.entry_id}"

    caplog.clear()
    with caplog.at_level("ERROR"):
        await hass.config_entries.async_remove(doorman_config_entry.entry_id)
        await hass.async_block_till_done()

        # The late history reply must not resurrect the removed store.
        release.set()
        await hass.async_block_till_done()

    assert task.cancelled()
    assert key not in hass_storage
    assert "Task exception" not in caplog.text
    assert [r.getMessage() for r in caplog.records if r.levelno >= 40] == []


# ─── doorman.resync_log_history ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resync_service_adds_new_events(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
    caplog,
) -> None:
    """The service merges freshly fetched history and reports what it added."""
    mock_2n_client.fetch_log_history = AsyncMock(return_value=[_HISTORY_EVENT])

    fired = []
    hass.bus.async_listen(f"{DOMAIN}_access", lambda e: fired.append(e))

    with caplog.at_level("INFO", logger="custom_components.doorman"):
        await hass.services.async_call(DOMAIN, "resync_log_history", {}, blocking=True)
    await hass.async_block_till_done()

    assert 42 in _stored_ids(hass, setup_doorman.entry_id)
    assert "1 new" in caplog.text
    # The no-notify guarantee holds for the on-demand resync too.
    assert fired == []


@pytest.mark.asyncio
async def test_resync_service_is_idempotent(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
    caplog,
) -> None:
    """Re-running against unchanged device history adds nothing and says so."""
    mock_2n_client.fetch_log_history = AsyncMock(return_value=[_HISTORY_EVENT])

    await hass.services.async_call(DOMAIN, "resync_log_history", {}, blocking=True)
    caplog.clear()
    with caplog.at_level("INFO", logger="custom_components.doorman"):
        await hass.services.async_call(DOMAIN, "resync_log_history", {}, blocking=True)

    assert _stored_ids(hass, setup_doorman.entry_id).count(42) == 1
    assert "already stored" in caplog.text


@pytest.mark.asyncio
async def test_resync_service_distinguishes_an_empty_device_reply(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
    caplog,
) -> None:
    """No history at all is logged differently from history with nothing new."""
    mock_2n_client.fetch_log_history = AsyncMock(return_value=[])

    with caplog.at_level("INFO", logger="custom_components.doorman"):
        await hass.services.async_call(DOMAIN, "resync_log_history", {}, blocking=True)

    assert "returned no log history" in caplog.text
    assert "already stored" not in caplog.text


@pytest.mark.asyncio
async def test_concurrent_resync_calls_do_not_overlap(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Hammering the service must not open two history subscriptions."""
    release, started = asyncio.Event(), asyncio.Event()
    calls = 0

    async def counted(**_kwargs) -> list[dict]:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return [_HISTORY_EVENT]

    mock_2n_client.fetch_log_history = counted

    first = asyncio.ensure_future(
        hass.services.async_call(DOMAIN, "resync_log_history", {}, blocking=True)
    )
    second = asyncio.ensure_future(
        hass.services.async_call(DOMAIN, "resync_log_history", {}, blocking=True)
    )
    await started.wait()
    await asyncio.sleep(0.01)
    release.set()
    async with asyncio.timeout(10):
        await asyncio.gather(first, second)

    assert calls == 1
    assert _stored_ids(hass, setup_doorman.entry_id).count(42) == 1


@pytest.mark.asyncio
async def test_resync_targets_only_the_requested_entry(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """In a two-device install only the targeted entry's store is touched."""
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)
    await hass.async_block_till_done()

    mock_2n_client.fetch_log_history = AsyncMock(return_value=[_HISTORY_EVENT])

    await hass.services.async_call(
        DOMAIN, "resync_log_history", {"device": entry2.entry_id}, blocking=True
    )

    assert 42 in _stored_ids(hass, entry2.entry_id)
    assert 42 not in _stored_ids(hass, entry1.entry_id)
    assert mock_2n_client.fetch_log_history.await_count == 1


@pytest.mark.asyncio
async def test_resync_with_unknown_device_raises(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """The service uses the same device-resolution rules as the others."""
    with pytest.raises(ServiceValidationError, match="Unknown Doorman device"):
        await hass.services.async_call(
            DOMAIN, "resync_log_history", {"device": "nope"}, blocking=True
        )


@pytest.mark.asyncio
async def test_answer_call_service(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """doorman.answer_call answers the ringing call via the client."""
    mock_2n_client.answer_ringing_call.return_value = True

    await hass.services.async_call(DOMAIN, "answer_call", {}, blocking=True)

    mock_2n_client.answer_ringing_call.assert_called_once()


@pytest.mark.asyncio
async def test_answer_call_service_api_error(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A device error from answer_call surfaces as HomeAssistantError."""
    from homeassistant.exceptions import HomeAssistantError

    from custom_components.doorman.api_client import DoormanApiError

    mock_2n_client.answer_ringing_call.side_effect = DoormanApiError("no privilege")

    with pytest.raises(HomeAssistantError, match="answer_call failed"):
        await hass.services.async_call(DOMAIN, "answer_call", {}, blocking=True)


@pytest.mark.asyncio
async def test_dial_service_with_number(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """doorman.dial forwards the number and reports the session."""
    await hass.services.async_call(
        DOMAIN, "dial", {"number": "sip:1234@10.0.0.5"}, blocking=True
    )

    mock_2n_client.dial.assert_called_once_with(number="sip:1234@10.0.0.5", users=None)


@pytest.mark.asyncio
async def test_dial_service_with_user_uuids(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """doorman.dial parses the comma-separated UUID list."""
    await hass.services.async_call(
        DOMAIN, "dial", {"user_uuids": "uuid-a, uuid-b"}, blocking=True
    )

    mock_2n_client.dial.assert_called_once_with(number=None, users=["uuid-a", "uuid-b"])


@pytest.mark.asyncio
async def test_call_state_changed_event_mapped(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """CallStateChanged reaches the event entity with call attributes."""
    coordinator = hass.data[DOMAIN][setup_doorman.entry_id]
    coordinator._fire_new_access_events([{
        "event": "CallStateChanged",
        "utcTime": 1743242400,
        "params": {
            "direction": "outgoing",
            "state": "ringing",
            "peer": "sip:2001@192.168.0.10",
            "session": 4,
        },
    }])
    await hass.async_block_till_done()

    state = hass.states.get("event.doorman_1012345678_access")
    assert state.attributes.get("event_type") == "call_state_changed"
    assert state.attributes.get("direction") == "outgoing"
    assert state.attributes.get("state") == "ringing"
    assert state.attributes.get("peer") == "sip:2001@192.168.0.10"
    assert state.attributes.get("session") == 4
