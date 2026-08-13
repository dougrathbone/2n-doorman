"""Tests for Doorman WebSocket API handlers — multi-device routing."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import CONF_POLL_INTERVAL, DOMAIN
from custom_components.doorman.websocket import (
    ws_get_access_log,
    ws_get_device_info,
    ws_get_notification_settings,
    ws_link_user,
    ws_list_devices,
    ws_list_ha_users,
    ws_list_notify_services,
    ws_list_users,
    ws_send_test_notification,
    ws_set_notification_settings,
    ws_set_notification_targets,
    ws_unlink_user,
)

from .conftest import MOCK_DEVICE_INFO, setup_two_entries


def _mock_connection(is_admin: bool = True):
    conn = MagicMock()
    conn.send_result = MagicMock()
    conn.send_error = MagicMock()
    conn.user = MagicMock()
    conn.user.is_admin = is_admin
    return conn


@pytest.mark.asyncio
async def test_ws_list_devices_single(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_list_devices returns one device when a single entry is configured."""
    conn = _mock_connection()
    ws_list_devices(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    result = conn.send_result.call_args[0][1]
    devices = result["devices"]
    assert len(devices) == 1
    assert devices[0]["entry_id"] == setup_doorman.entry_id
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
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_list_users without entry_id returns users from the first device."""
    conn = _mock_connection()
    ws_list_users(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    users = conn.send_result.call_args[0][1]["users"]
    assert len(users) == 2


@pytest.mark.asyncio
async def test_ws_list_users_invalid_entry_id(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_list_users with a nonexistent entry_id returns not_configured error."""
    conn = _mock_connection()
    ws_list_users(hass, conn, {"id": 1, "entry_id": "nonexistent"})

    conn.send_error.assert_called_once()
    args = conn.send_error.call_args[0]
    assert args[1] == "not_configured"


@pytest.mark.asyncio
async def test_ws_get_device_info_with_entry_id(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_device_info with entry_id returns info for the specified device."""
    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1, "entry_id": setup_doorman.entry_id})

    conn.send_result.assert_called_once()
    info = conn.send_result.call_args[0][1]["device_info"]
    assert info["serialNumber"] == MOCK_DEVICE_INFO["serialNumber"]


@pytest.mark.asyncio
async def test_ws_get_access_log_with_entry_id(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_access_log with entry_id routes to the specified device."""
    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1, "entry_id": setup_doorman.entry_id})
    await hass.async_block_till_done()

    conn.send_result.assert_called_once()
    assert "events" in conn.send_result.call_args[0][1]


@pytest.mark.asyncio
async def test_ws_get_access_log_without_entry_id(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_access_log without entry_id falls back to the first device."""
    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    assert "events" in conn.send_result.call_args[0][1]


@pytest.mark.asyncio
async def test_ws_get_access_log_invalid_entry_id(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_access_log with an unknown entry_id returns not_configured."""
    conn = _mock_connection()
    ws_get_access_log(hass, conn, {"id": 1, "entry_id": "nonexistent"})

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "not_configured"


@pytest.mark.asyncio
async def test_ws_get_device_info_includes_access_points(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_device_info response includes the access_points list."""
    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1, "entry_id": setup_doorman.entry_id})

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
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_device_info without entry_id falls back to the first device."""
    conn = _mock_connection()
    ws_get_device_info(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    info = conn.send_result.call_args[0][1]["device_info"]
    assert info["serialNumber"] == MOCK_DEVICE_INFO["serialNumber"]


@pytest.mark.asyncio
async def test_ws_get_device_info_invalid_entry_id(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_get_device_info with an unknown entry_id returns not_configured."""
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


# ------------------------------------------------------------------ #
# ws_list_ha_users                                                     #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_ws_list_ha_users_returns_users(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_list_ha_users returns active, non-system HA users."""
    # Create a mock HA user via the auth system
    user = await hass.auth.async_create_user("Test User")

    conn = _mock_connection(is_admin=True)
    ws_list_ha_users(hass, conn, {"id": 1})
    await hass.async_block_till_done()

    conn.send_result.assert_called_once()
    result = conn.send_result.call_args[0][1]
    assert "users" in result
    user_ids = [u["id"] for u in result["users"]]
    assert user.id in user_ids


@pytest.mark.asyncio
async def test_ws_list_ha_users_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_list_ha_users rejects non-admin connections."""
    conn = _mock_connection(is_admin=False)
    ws_list_ha_users(hass, conn, {"id": 1})
    await hass.async_block_till_done()

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "unauthorized"


# ------------------------------------------------------------------ #
# ws_link_user / ws_unlink_user                                        #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_ws_link_user(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_link_user links a 2N user to an HA user, visible via ws_list_users."""
    conn = _mock_connection(is_admin=True)
    ws_link_user(hass, conn, {"id": 1, "two_n_uuid": "uuid-jane", "ha_user_id": "ha-user-1"})
    await hass.async_block_till_done()

    conn.send_result.assert_called_once()
    assert conn.send_result.call_args[0][1]["success"] is True

    # Verify through ws_list_users
    conn2 = _mock_connection()
    ws_list_users(hass, conn2, {"id": 2})
    users = conn2.send_result.call_args[0][1]["users"]
    jane = next(u for u in users if u["uuid"] == "uuid-jane")
    assert jane["ha_user_id"] == "ha-user-1"


@pytest.mark.asyncio
async def test_ws_unlink_user(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_unlink_user removes a previously linked HA user."""
    conn = _mock_connection(is_admin=True)

    # Link first
    ws_link_user(hass, conn, {"id": 1, "two_n_uuid": "uuid-jane", "ha_user_id": "ha-user-1"})
    await hass.async_block_till_done()

    # Unlink
    conn2 = _mock_connection(is_admin=True)
    ws_unlink_user(hass, conn2, {"id": 2, "two_n_uuid": "uuid-jane"})
    await hass.async_block_till_done()
    conn2.send_result.assert_called_once()
    assert conn2.send_result.call_args[0][1]["success"] is True

    # Verify the link is gone
    conn3 = _mock_connection()
    ws_list_users(hass, conn3, {"id": 3})
    users = conn3.send_result.call_args[0][1]["users"]
    jane = next(u for u in users if u["uuid"] == "uuid-jane")
    assert jane["ha_user_id"] is None


# ------------------------------------------------------------------ #
# ws_list_notify_services                                              #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_ws_list_notify_services(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_list_notify_services returns a services list."""
    conn = _mock_connection()
    ws_list_notify_services(hass, conn, {"id": 1})

    conn.send_result.assert_called_once()
    result = conn.send_result.call_args[0][1]
    assert "services" in result
    assert isinstance(result["services"], list)


# ------------------------------------------------------------------ #
# ws_set_notification_targets                                          #
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_ws_set_notification_targets(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_set_notification_targets persists targets, visible inline via list_users."""
    conn = _mock_connection(is_admin=True)
    ws_set_notification_targets(
        hass, conn, {"id": 1, "two_n_uuid": "uuid-jane", "targets": ["notify.mobile_app"]}
    )
    await hass.async_block_till_done()

    conn.send_result.assert_called_once()
    assert conn.send_result.call_args[0][1]["success"] is True

    # Targets arrive inline on the user objects returned by list_users
    conn2 = _mock_connection()
    ws_list_users(hass, conn2, {"id": 2})
    users = conn2.send_result.call_args[0][1]["users"]
    jane = next(u for u in users if u["uuid"] == "uuid-jane")
    assert jane["notification_targets"] == ["notify.mobile_app"]


@pytest.mark.asyncio
async def test_ws_set_notification_targets_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """ws_set_notification_targets rejects non-admin connections."""
    conn = _mock_connection(is_admin=False)
    ws_set_notification_targets(
        hass, conn, {"id": 1, "two_n_uuid": "uuid-jane", "targets": ["notify.mobile_app"]}
    )
    await hass.async_block_till_done()

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "unauthorized"


@pytest.mark.asyncio
async def test_read_commands_reject_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Sensitive read commands (which expose PINs/cards/codes) reject non-admin users."""
    conn = _mock_connection(is_admin=False)

    ws_list_users(hass, conn, {"id": 1})
    ws_get_access_log(hass, conn, {"id": 2})
    ws_get_device_info(hass, conn, {"id": 3})
    ws_list_devices(hass, conn, {"id": 4})

    assert conn.send_error.call_count == 4
    assert all(c.args[1] == "unauthorized" for c in conn.send_error.call_args_list)
    conn.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_no_entry_id_with_multiple_devices_returns_not_configured(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """With multiple devices and no entry_id, commands refuse to guess a device."""
    await setup_two_entries(hass, doorman_config_entry)

    conn = _mock_connection()
    ws_list_users(hass, conn, {"id": 1})
    ws_get_access_log(hass, conn, {"id": 2})
    ws_get_device_info(hass, conn, {"id": 3})

    assert conn.send_error.call_count == 3
    assert all(c.args[1] == "not_configured" for c in conn.send_error.call_args_list)
    conn.send_result.assert_not_called()


# ------------------------------------------------------------------ #
# Notification settings — driven through a real WebSocket client       #
# ------------------------------------------------------------------ #
#
# These go through hass_ws_client rather than calling the handlers with a
# MagicMock connection so the voluptuous schemas are actually exercised: a
# direct call bypasses validation entirely, which is how type-only schemas
# came to ship untested.


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_get_notification_settings_returns_defaults(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """A fresh entry returns default settings + a populated iOS sound catalog."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "doorman/get_notification_settings"})
    res = await client.receive_json()

    assert res["success"], res
    result = res["result"]
    assert result["device_name"] == setup_doorman.title
    assert result["settings"] == {
        "access_sound_ios": "",
        "access_channel_android": "",
        "doorbell_sound_ios": "",
        "doorbell_channel_android": "",
        "doorbell_key_code": "%1",
        "doorbell_targets": [],
    }
    # Catalog is grouped; sanity-check the shape
    assert len(result["ios_sound_catalog"]) >= 1
    first_group = result["ios_sound_catalog"][0]
    assert "group" in first_group and "sounds" in first_group
    assert isinstance(result["notify_services"], list)


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_set_notification_settings_round_trips_via_the_store(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """Saved settings come back from a later get, and land in DoormanStore."""
    settings_in = {
        "access_sound_ios": "US-EN-Alexa-Front-Door-Opened.wav",
        "access_channel_android": "doorman_access",
        "doorbell_sound_ios": "US-EN-Alexa-Mail-Has-Arrived.wav",
        "doorbell_channel_android": "doorman_doorbell",
        "doorbell_key_code": "%2",
        "doorbell_targets": ["notify.mobile_app"],
    }
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "doorman/set_notification_settings", "settings": settings_in}
    )
    res = await client.receive_json()
    assert res["success"], res
    assert res["result"]["settings"] == settings_in

    await client.send_json_auto_id({"type": "doorman/get_notification_settings"})
    res = await client.receive_json()
    assert res["result"]["settings"] == settings_in

    # Persisted in the store, keyed by entry_id — and nowhere near entry.options.
    store = hass.data[f"{DOMAIN}_store"]
    assert store.get_notification_settings(setup_doorman.entry_id) == settings_in
    assert not set(settings_in) & set(setup_doorman.options)


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_set_notification_settings_merges_partial_updates(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """A save that carries one field must not blank the others."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "doorman/set_notification_settings",
            "settings": {"doorbell_targets": ["notify.mobile_app"], "doorbell_key_code": "%3"},
        }
    )
    assert (await client.receive_json())["success"]

    await client.send_json_auto_id(
        {"type": "doorman/set_notification_settings", "settings": {"access_sound_ios": "a.wav"}}
    )
    res = await client.receive_json()

    assert res["result"]["settings"]["doorbell_targets"] == ["notify.mobile_app"]
    assert res["result"]["settings"]["doorbell_key_code"] == "%3"
    assert res["result"]["settings"]["access_sound_ios"] == "a.wav"


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_set_notification_settings_does_not_reload_the_entry(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """Saving must not reload the entry.

    A reload tears down the 2N log subscription; a fresh subscription starts
    empty with no watermark, so every event in the reload window is lost. On a
    single-entry install it also unregisters the sidebar panel, drops the
    store, and removes the doorman.* services out from under the user.
    """
    coordinator_before = hass.data[DOMAIN][setup_doorman.entry_id]
    log_task_before = coordinator_before._log_task
    assert log_task_before is not None and not log_task_before.done()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "doorman/set_notification_settings", "settings": {"doorbell_key_code": "%4"}}
    )
    assert (await client.receive_json())["success"]
    await hass.async_block_till_done()

    # Same coordinator object, same still-running listener task.
    assert hass.data[DOMAIN][setup_doorman.entry_id] is coordinator_before
    assert coordinator_before._log_task is log_task_before
    assert not log_task_before.done()
    # And the panel / services / store the user is standing on are still there.
    assert hass.data.get(f"{DOMAIN}_panel_registered") is True
    assert hass.services.has_service(DOMAIN, "create_user")
    assert hass.data.get(f"{DOMAIN}_store") is not None


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_set_notification_settings_preserves_poll_interval(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
    hass_ws_client,
) -> None:
    """Notification settings never touch entry.options, so poll_interval survives."""
    doorman_config_entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        doorman_config_entry, options={CONF_POLL_INTERVAL: 45}
    )
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "doorman/set_notification_settings",
            "settings": {"doorbell_key_code": "%2", "doorbell_targets": ["notify.mobile_app"]},
        }
    )
    assert (await client.receive_json())["success"]

    assert doorman_config_entry.options == {CONF_POLL_INTERVAL: 45}


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_set_notification_settings_rejects_bad_input(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """The schema rejects malformed settings instead of persisting them."""
    client = await hass_ws_client(hass)
    bad_payloads = [
        # A bare keypad digit would make every PIN keystroke ring the doorbell.
        {"doorbell_key_code": "5"},
        {"doorbell_key_code": "%"},
        # The panel's UI-only sentinel must never reach data.push.sound.
        {"doorbell_sound_ios": "__custom__"},
        {"access_channel_android": "__custom__"},
        # doorbell_targets is a list of strings, not a bare string.
        {"doorbell_targets": "notify.mobile_app"},
        # Unknown keys are rejected rather than silently dropped.
        {"totally_unknown": "x"},
    ]
    for payload in bad_payloads:
        await client.send_json_auto_id(
            {"type": "doorman/set_notification_settings", "settings": payload}
        )
        res = await client.receive_json()
        assert not res["success"], payload
        assert res["error"]["code"] == "invalid_format", payload

    # Nothing was persisted by any of the rejected calls.
    store = hass.data[f"{DOMAIN}_store"]
    assert store.get_notification_settings(setup_doorman.entry_id)["doorbell_key_code"] == "%1"


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_set_notification_settings_accepts_empty_doorbell_key(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """An empty doorbell key is valid: it means "this device has no doorbell button"."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "doorman/set_notification_settings", "settings": {"doorbell_key_code": ""}}
    )
    res = await client.receive_json()

    assert res["success"], res
    assert res["result"]["settings"]["doorbell_key_code"] == ""


@pytest.mark.asyncio
async def test_ws_get_notification_settings_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Notification settings expose the configured notify targets — admin only."""
    conn = _mock_connection(is_admin=False)
    ws_get_notification_settings(hass, conn, {"id": 1})

    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "unauthorized"
    conn.send_result.assert_not_called()


@pytest.mark.asyncio
async def test_ws_set_notification_settings_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    conn = _mock_connection(is_admin=False)
    ws_set_notification_settings(
        hass, conn, {"id": 1, "settings": {"doorbell_key_code": "%3"}}
    )
    await hass.async_block_till_done()
    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "unauthorized"


# ------------------------------------------------------------------ #
# ws_send_test_notification                                            #
# ------------------------------------------------------------------ #


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_send_test_notification_dispatches_with_sound(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """The Preview button calls notify.<service> with the chosen sound + channel."""
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "doorman/send_test_notification",
            "target": "notify.mobile_app",
            "title": "Doorbell",
            "message": "Front Door: someone rang the doorbell",
            "ios_sound": "US-EN-Alexa-Mail-Has-Arrived.wav",
            "android_channel": "doorbell",
        }
    )
    res = await client.receive_json()

    assert res["success"], res
    assert len(calls) == 1
    assert calls[0].data["title"] == "Doorbell"
    assert calls[0].data["data"]["push"] == {"sound": "US-EN-Alexa-Mail-Has-Arrived.wav"}
    assert calls[0].data["data"]["channel"] == "doorbell"


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_send_test_notification_surfaces_notify_failure(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """A failing notify handler must fail the Preview, not toast "sent".

    Regression guard: the call has to be blocking. Fire-and-forget sends the
    success result before the notify service has run, so the panel reports
    success for a notification that never left the building (expired push
    token, unregistered device, rejected channel) — which defeats the entire
    purpose of a Preview button.
    """
    def _raise(call):
        raise HomeAssistantError("device not registered")

    hass.services.async_register("notify", "mobile_app", _raise)

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "doorman/send_test_notification",
            "target": "notify.mobile_app",
            "title": "Doorbell",
            "message": "test",
        }
    )
    res = await client.receive_json()

    assert not res["success"]
    assert res["error"]["code"] == "notify_failed"
    assert "device not registered" in res["error"]["message"]


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_send_test_notification_requires_a_target(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """vol.Required("target") is enforced on the wire."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {"type": "doorman/send_test_notification", "title": "t", "message": "m"}
    )
    res = await client.receive_json()

    assert not res["success"]
    assert res["error"]["code"] == "invalid_format"


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_send_test_notification_rejects_ui_sentinel_sound(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """The panel's "__custom__" placeholder must never reach data.push.sound."""
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "doorman/send_test_notification",
            "target": "notify.mobile_app",
            "title": "t",
            "message": "m",
            "ios_sound": "__custom__",
        }
    )
    res = await client.receive_json()

    assert not res["success"]
    assert res["error"]["code"] == "invalid_format"
    assert calls == []


@pytest.mark.real_http
@pytest.mark.asyncio
async def test_ws_send_test_notification_reports_unknown_target(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    hass_ws_client,
) -> None:
    """Preview with a stale target returns unknown_target rather than raising."""
    client = await hass_ws_client(hass)
    await client.send_json_auto_id(
        {
            "type": "doorman/send_test_notification",
            "target": "notify.gone_service",
            "title": "t",
            "message": "m",
        }
    )
    res = await client.receive_json()

    assert not res["success"]
    assert res["error"]["code"] == "unknown_target"


@pytest.mark.asyncio
async def test_ws_send_test_notification_rejects_non_admin(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    conn = _mock_connection(is_admin=False)
    ws_send_test_notification(
        hass, conn,
        {"id": 1, "target": "notify.mobile_app", "title": "t", "message": "m"},
    )
    await hass.async_block_till_done()
    conn.send_error.assert_called_once()
    assert conn.send_error.call_args[0][1] == "unauthorized"

