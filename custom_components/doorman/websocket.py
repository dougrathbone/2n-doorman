"""WebSocket API handlers for the Doorman sidebar panel."""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN
from .coordinator import DoormanCoordinator
from .storage import DoormanStore


def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register all Doorman WebSocket commands."""
    websocket_api.async_register_command(hass, ws_list_users)
    websocket_api.async_register_command(hass, ws_get_device_info)
    websocket_api.async_register_command(hass, ws_get_access_log)
    websocket_api.async_register_command(hass, ws_list_ha_users)
    websocket_api.async_register_command(hass, ws_link_user)
    websocket_api.async_register_command(hass, ws_unlink_user)


def _coordinator(hass: HomeAssistant) -> DoormanCoordinator | None:
    entries = hass.data.get(DOMAIN, {})
    return next(iter(entries.values()), None)


def _store(hass: HomeAssistant) -> DoormanStore | None:
    return hass.data.get(f"{DOMAIN}_store")


# ------------------------------------------------------------------ #
# Users                                                               #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_users"})
@callback
def ws_list_users(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all 2N directory users, annotated with their linked HA user ID."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    store = _store(hass)
    links = store.user_links if store else {}

    users = [
        {**user, "ha_user_id": links.get(user.get("uuid"))}
        for user in coordinator.data.get("users", [])
    ]
    connection.send_result(msg["id"], {"users": users})


# ------------------------------------------------------------------ #
# Device info                                                          #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_device_info"})
@callback
def ws_get_device_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return static device information (model, firmware, serial)."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return
    connection.send_result(msg["id"], {"device_info": coordinator.device_info})


# ------------------------------------------------------------------ #
# Access log                                                           #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/get_access_log"})
@websocket_api.async_response
async def ws_get_access_log(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Fetch the most recent access log events directly from the device."""
    coordinator = _coordinator(hass)
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return
    events = await coordinator.client.pull_log(count=200)
    connection.send_result(msg["id"], {"events": events})


# ------------------------------------------------------------------ #
# HA user management (admin only)                                     #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_ha_users"})
@callback
def ws_list_ha_users(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all active, non-system HA user accounts."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    # Access the internal auth store to list human users
    ha_users = [
        {"id": u.id, "name": u.name}
        for u in hass.auth._store._users.values()  # noqa: SLF001
        if not u.system_generated and u.is_active
    ]
    connection.send_result(msg["id"], {"users": ha_users})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/link_user",
        vol.Required("two_n_uuid"): str,
        vol.Required("ha_user_id"): str,
    }
)
@websocket_api.async_response
async def ws_link_user(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Link a 2N directory user to a Home Assistant user account."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    store = _store(hass)
    if store is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    await store.link_user(msg["two_n_uuid"], msg["ha_user_id"])
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/unlink_user",
        vol.Required("two_n_uuid"): str,
    }
)
@websocket_api.async_response
async def ws_unlink_user(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Remove the HA user link for a 2N directory entry."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    store = _store(hass)
    if store is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    await store.unlink_user(msg["two_n_uuid"])
    connection.send_result(msg["id"], {"success": True})
