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
    """HA REST API responds and reports a valid version."""
    info = await ha.get("/api/")
    assert "version" in info, f"Expected 'version' in /api/ response, got: {info}"


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

    payload = create_calls[0]["body"]["user"]
    assert payload["name"] == "New Resident"
    assert payload["pin"] == "5678"

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

    payload = update_calls[0]["body"]["user"]
    assert payload["uuid"] == "uuid-test-01"
    assert payload["name"] == "Updated Name"
    assert payload["pin"] == "9999"


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
    assert delete_calls[0]["body"]["uuid"] == "uuid-test-01"

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

    # Verify state updated in HA
    await asyncio.sleep(2)
    state = await ha.get_state("switch.doorman_relay_1")
    assert state["state"] == "on"


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
async def test_ws_get_access_log_returns_list(ws: HaWebSocket) -> None:
    """doorman/get_access_log WS command returns a list (empty on mock server)."""
    result = await ws.command("doorman/get_access_log")
    assert "events" in result
    assert isinstance(result["events"], list)


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
