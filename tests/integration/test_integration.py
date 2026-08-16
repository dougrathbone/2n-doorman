"""Full supervisor integration tests for the Doorman integration.

These tests run against a real Home Assistant instance (started via
docker-compose) with a real custom component installation and a mock
2N API server. They validate end-to-end behaviour: config entry loading,
entity creation, service calls, WebSocket commands, and side-effects on
the mock device.

Run locally:
    docker compose -f tests/integration/docker-compose.yml up -d --wait
    pytest tests/integration/ -v

In CI this is driven by .github/workflows/integration.yml.
"""
from __future__ import annotations

import asyncio

import pytest

from .helpers import HaClient, HaWebSocket, Mock2nAdmin

# ─── Sanity / setup validation ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ha_is_running(ha: HaClient) -> None:
    """HA REST API responds."""
    info = await ha.get("/api/")
    assert isinstance(info, dict), f"Expected a dict from /api/, got: {info}"


@pytest.mark.asyncio
async def test_mock_2n_is_running(mock_2n: Mock2nAdmin) -> None:
    """Mock 2N server is healthy and returns initial state."""
    users = await mock_2n.get_users()
    assert len(users) == 1
    assert users[0]["name"] == "Test User"


# ─── Config entry ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_doorman_config_entry_is_loaded(ha: HaClient) -> None:
    """The Doorman config entry exists and has state 'loaded'."""
    entries = await ha.get_config_entries()
    doorman_entries = [e for e in entries if e["domain"] == "doorman"]
    assert doorman_entries, "No Doorman config entry found"
    assert doorman_entries[0]["state"] == "loaded", (
        f"Expected state=loaded, got: {doorman_entries[0]['state']}"
    )
    assert doorman_entries[0]["title"] == "2N IP Verso (Test)"


# ─── Entity creation ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_sensor_user_count_exists(ha: HaClient) -> None:
    """sensor.doorman_user_count exists and reflects the mock server's initial user."""
    state = await ha.wait_for_state("sensor.doorman_user_count", timeout=30)
    assert state["state"] == "1", f"Expected 1 user, got: {state['state']}"
    assert "users" in state["attributes"]
    assert state["attributes"]["users"][0]["name"] == "Test User"


@pytest.mark.asyncio
async def test_relay_switch_exists(ha: HaClient) -> None:
    """switch.doorman_relay_1 exists and reflects the mock server's initial switch state."""
    state = await ha.wait_for_state("switch.doorman_relay_1", timeout=30)
    assert state["state"] == "off"


# ─── Services ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_service_adds_user_to_device(
    ha: HaClient,
    mock_2n: Mock2nAdmin,
) -> None:
    """doorman.create_user adds a user to the 2N device and updates the sensor."""
    await ha.call_service("doorman", "create_user", {
        "name": "New Resident",
        "pin": "5678",
    })

    # Mock server should have received a create_dir call
    calls = await mock_2n.get_calls()
    create_calls = [c for c in calls if c["path"] == "/api/dir/create"]
    assert create_calls, "Expected a PUT /api/dir/create call on the mock server"

    payload = create_calls[0]["body"]["users"][0]
    assert payload["name"] == "New Resident"
    assert payload["access"]["pin"] == "5678"

    # Wait for coordinator to refresh and sensor to update
    await asyncio.sleep(2)
    users = await mock_2n.get_users()
    assert any(u["name"] == "New Resident" for u in users)


@pytest.mark.asyncio
async def test_update_user_service_updates_device(
    ha: HaClient,
    mock_2n: Mock2nAdmin,
) -> None:
    """doorman.update_user sends the updated fields to the 2N device."""
    await ha.call_service("doorman", "update_user", {
        "uuid": "uuid-test-01",
        "name": "Updated Name",
        "pin": "9999",
    })

    calls = await mock_2n.get_calls()
    update_calls = [c for c in calls if c["path"] == "/api/dir/update"]
    assert update_calls, "Expected a PUT /api/dir/update call"

    payload = update_calls[0]["body"]["users"][0]
    assert payload["uuid"] == "uuid-test-01"
    assert payload["name"] == "Updated Name"
    assert payload["access"]["pin"] == "9999"


@pytest.mark.asyncio
async def test_delete_user_service_removes_user_from_device(
    ha: HaClient,
    mock_2n: Mock2nAdmin,
) -> None:
    """doorman.delete_user removes the user from the 2N directory."""
    await ha.call_service("doorman", "delete_user", {"uuid": "uuid-test-01"})

    calls = await mock_2n.get_calls()
    delete_calls = [c for c in calls if c["path"] == "/api/dir/delete"]
    assert delete_calls, "Expected a PUT /api/dir/delete call"
    assert delete_calls[0]["body"]["users"][0]["uuid"] == "uuid-test-01"

    users = await mock_2n.get_users()
    assert not any(u["uuid"] == "uuid-test-01" for u in users)


@pytest.mark.asyncio
async def test_grant_access_service_calls_device(
    ha: HaClient,
    mock_2n: Mock2nAdmin,
) -> None:
    """doorman.grant_access triggers the correct access point on the device."""
    await ha.call_service("doorman", "grant_access", {"access_point_id": 1})

    calls = await mock_2n.get_calls()
    access_calls = [c for c in calls if c["path"] == "/api/accesspoint/grantaccess"]
    assert access_calls, "Expected a GET /api/accesspoint/grantaccess call"
    assert access_calls[0]["body"]["id"] == "1"


@pytest.mark.asyncio
async def test_relay_switch_turn_on(
    ha: HaClient,
    mock_2n: Mock2nAdmin,
) -> None:
    """Turning on switch.doorman_relay_1 sends action=on to the device."""
    await ha.call_service("switch", "turn_on", {"entity_id": "switch.doorman_relay_1"})

    calls = await mock_2n.get_calls()
    ctrl_calls = [c for c in calls if c["path"] == "/api/switch/ctrl"]
    assert ctrl_calls, "Expected a GET /api/switch/ctrl call"
    assert ctrl_calls[0]["body"]["action"] == "on"

    # Wait for coordinator to refresh and entity state to reflect the change
    await ha.wait_for_state_value("switch.doorman_relay_1", "on", timeout=30)


# ─── WebSocket commands ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_ws_list_users_returns_directory(ws: HaWebSocket) -> None:
    """doorman/list_users WS command returns the current directory from the device."""
    result = await ws.command("doorman/list_users")
    assert "users" in result
    assert len(result["users"]) >= 1
    assert result["users"][0]["name"] == "Test User"


@pytest.mark.asyncio
async def test_ws_get_device_info_returns_model(ws: HaWebSocket) -> None:
    """doorman/get_device_info WS command returns the device model and firmware."""
    result = await ws.command("doorman/get_device_info")
    assert "device_info" in result
    info = result["device_info"]
    assert info["deviceName"] == "2N IP Verso (Test)"
    assert info["serialNumber"] == "10-99999999"


@pytest.mark.asyncio
async def test_ws_get_access_log_returns_backfilled_history(ws: HaWebSocket) -> None:
    """The startup backfill pulls the device's on-box history into the log."""
    result = await ws.command("doorman/get_access_log")
    assert "events" in result
    assert isinstance(result["events"], list)
    # The mock device has two historical events recorded before HA started.
    event_types = [e.get("event") for e in result["events"]]
    assert "UserAuthenticated" in event_types
    assert "CardEntered" in event_types


@pytest.mark.asyncio
async def test_ws_list_ha_users_admin_only(ws: HaWebSocket) -> None:
    """doorman/list_ha_users returns the test admin account (admin-only command)."""
    result = await ws.command("doorman/list_ha_users")
    assert "users" in result
    names = [u["name"] for u in result["users"]]
    assert "Test Admin" in names


@pytest.mark.asyncio
async def test_ws_link_and_unlink_user(ws: HaWebSocket) -> None:
    """Linking and then unlinking a 2N user to an HA user persists correctly."""
    # Get the HA user ID for 'Test Admin'
    ha_users_result = await ws.command("doorman/list_ha_users")
    admin = next(u for u in ha_users_result["users"] if u["name"] == "Test Admin")

    # Link
    await ws.command(
        "doorman/link_user",
        two_n_uuid="uuid-test-01",
        ha_user_id=admin["id"],
    )

    # Verify link appears in list_users
    users_result = await ws.command("doorman/list_users")
    linked = next(u for u in users_result["users"] if u["uuid"] == "uuid-test-01")
    assert linked["ha_user_id"] == admin["id"]

    # Unlink
    await ws.command("doorman/unlink_user", two_n_uuid="uuid-test-01")

    # Verify link is removed
    users_result = await ws.command("doorman/list_users")
    unlinked = next(u for u in users_result["users"] if u["uuid"] == "uuid-test-01")
    assert unlinked["ha_user_id"] is None


# ─── Call control ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hangup_calls_service(
    ha: HaClient,
    mock_2n: Mock2nAdmin,
) -> None:
    """doorman.hangup_calls hangs up the active session on the device."""
    await ha.call_service("doorman", "hangup_calls", {})

    calls = await mock_2n.get_calls()
    hangup_calls = [c for c in calls if c["path"] == "/api/call/hangup"]
    assert hangup_calls, "Expected a GET /api/call/hangup call"
    assert hangup_calls[0]["body"]["session"] == 1


# ─── Camera ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_camera_entity_exists_and_snapshots(ha: HaClient, mock_2n: Mock2nAdmin) -> None:
    """camera.doorman_camera exists and a snapshot request reaches the device."""
    state = await ha.wait_for_state("camera.doorman_camera", timeout=30)
    assert state["state"] == "idle"

    # Fetch the image through HA's camera proxy
    assert ha._session is not None
    async with ha._session.get(f"{ha.base_url}/api/camera_proxy/camera.doorman_camera") as resp:
        assert resp.status == 200
        body = await resp.read()
        assert body.startswith(b"\xff\xd8"), "Expected JPEG data from the camera proxy"

    calls = await mock_2n.get_calls()
    assert any(c["path"] == "/api/camera/snapshot" for c in calls)


# ─── Door & I/O binary sensors, event-driven state ──────────────────────────

@pytest.mark.asyncio
async def test_door_and_input_sensors_exist(ha: HaClient) -> None:
    """Door and input binary sensors are registered."""
    door = await ha.wait_for_state("binary_sensor.doorman_door", timeout=30)
    assert door["state"] == "unknown"  # no DoorStateChanged yet
    inp = await ha.wait_for_state("binary_sensor.doorman_input_input1", timeout=30)
    assert inp["state"] == "off"


@pytest.mark.asyncio
async def test_door_sensor_follows_injected_event(ha: HaClient, mock_2n: Mock2nAdmin) -> None:
    """Injected DoorStateChanged drives the door sensor in near-real-time."""
    await mock_2n.inject_event("DoorStateChanged", {"state": "opened"})
    await ha.wait_for_state_value("binary_sensor.doorman_door", "on", timeout=30)

    await mock_2n.inject_event("DoorStateChanged", {"state": "closed"})
    await ha.wait_for_state_value("binary_sensor.doorman_door", "off", timeout=30)


@pytest.mark.asyncio
async def test_input_sensor_follows_injected_event(ha: HaClient, mock_2n: Mock2nAdmin) -> None:
    """Injected InputChanged flips the input sensor without a poll cycle."""
    await mock_2n.inject_event("InputChanged", {"port": "input1", "state": True})
    await ha.wait_for_state_value("binary_sensor.doorman_input_input1", "on", timeout=30)


@pytest.mark.asyncio
async def test_switch_state_event_updates_relay_immediately(
    ha: HaClient, mock_2n: Mock2nAdmin
) -> None:
    """Injected SwitchStateChanged updates the relay entity without a poll."""
    # Prior state is unknowable here (an earlier test may have toggled the
    # relay), so drive both transitions rather than assuming an initial state.
    await mock_2n.inject_event(
        "SwitchStateChanged", {"switch": 1, "state": True, "originator": "auth"}
    )
    await ha.wait_for_state_value("switch.doorman_relay_1", "on", timeout=30)
    await mock_2n.inject_event(
        "SwitchStateChanged", {"switch": 1, "state": False, "originator": "api"}
    )
    await ha.wait_for_state_value("switch.doorman_relay_1", "off", timeout=30)


@pytest.mark.asyncio
async def test_security_event_reaches_event_entity(ha: HaClient, mock_2n: Mock2nAdmin) -> None:
    """Injected UnauthorizedDoorOpen surfaces on the access event entity."""
    await mock_2n.inject_event("UnauthorizedDoorOpen", {"state": "in"})
    state = await ha.wait_for_state_attr(
        "event.doorman_access", "event_type", "unauthorized_door_open", timeout=30
    )
    assert state["attributes"].get("state") == "in"
