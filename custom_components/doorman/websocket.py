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
    if hass.data.get(f"{DOMAIN}_websocket_registered"):
        return
    hass.data[f"{DOMAIN}_websocket_registered"] = True
    websocket_api.async_register_command(hass, ws_list_devices)
    websocket_api.async_register_command(hass, ws_list_users)
    websocket_api.async_register_command(hass, ws_get_device_info)
    websocket_api.async_register_command(hass, ws_get_access_log)
    websocket_api.async_register_command(hass, ws_list_ha_users)
    websocket_api.async_register_command(hass, ws_link_user)
    websocket_api.async_register_command(hass, ws_unlink_user)
    websocket_api.async_register_command(hass, ws_list_notify_services)
    websocket_api.async_register_command(hass, ws_get_notification_targets)
    websocket_api.async_register_command(hass, ws_set_notification_targets)


def _coordinator(hass: HomeAssistant, entry_id: str | None = None) -> DoormanCoordinator | None:
    entries = hass.data.get(DOMAIN, {})
    if entry_id:
        return entries.get(entry_id)
    return next(iter(entries.values()), None)


def _store(hass: HomeAssistant) -> DoormanStore | None:
    return hass.data.get(f"{DOMAIN}_store")


# ------------------------------------------------------------------ #
# Devices                                                              #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_devices"})
@callback
def ws_list_devices(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all configured Doorman device entries."""
    entries: dict[str, DoormanCoordinator] = hass.data.get(DOMAIN, {})
    devices = []
    for entry_id, coord in entries.items():
        device = {
            "entry_id": entry_id,
            "serial_number": coord.device_info.get("serialNumber", ""),
            "device_name": coord.device_info.get("deviceName", ""),
            "model": coord.device_info.get("hwVersion", ""),
        }
        devices.append(device)
    connection.send_result(msg["id"], {"devices": devices})


# ------------------------------------------------------------------ #
# Users                                                               #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/list_users",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_list_users(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all 2N directory users, annotated with their linked HA user ID."""
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    store = _store(hass)
    links = store.user_links if store else {}

    last_access = (coordinator.data or {}).get("last_access", {})
    users = [
        {
            **user,
            "ha_user_id": links.get(user.get("uuid")),
            "notification_targets": store.get_notification_targets(user.get("uuid", "")) if store else [],
            "last_access": last_access.get(user.get("uuid")),
        }
        for user in (coordinator.data or {}).get("users", [])
    ]
    connection.send_result(msg["id"], {
        "users": users,
        "write_permission": coordinator.has_write_permission,
    })


# ------------------------------------------------------------------ #
# Device info                                                          #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_device_info",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_get_device_info(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return static device information (model, firmware, serial)."""
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return
    connection.send_result(msg["id"], {
        "device_info": coordinator.device_info,
        "write_permission": coordinator.has_write_permission,
        "access_points": coordinator.access_points,
    })


# ------------------------------------------------------------------ #
# Access log                                                           #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_access_log",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_get_access_log(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return the access log events from the coordinator's last poll.

    Events are accumulated by the coordinator across polls and returned here.
    Calling pull_log() directly from the panel would race with the coordinator
    and consume events from the shared subscription, causing missed bus events.
    """
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return
    events = (coordinator.data or {}).get("log_events", [])
    connection.send_result(msg["id"], {"events": events})


# ------------------------------------------------------------------ #
# HA user management (admin only)                                     #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_ha_users"})
@websocket_api.async_response
async def ws_list_ha_users(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all active, non-system HA user accounts."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    all_users = await hass.auth.async_get_users()
    ha_users = [
        {"id": u.id, "name": u.name}
        for u in all_users
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


# ------------------------------------------------------------------ #
# Notification targets                                                 #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list_notify_services"})
@callback
def ws_list_notify_services(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return all registered notify.* service targets."""
    notify_services = list(hass.services.async_services().get("notify", {}).keys())
    targets = [f"notify.{s}" for s in notify_services if s not in ("notify", "send_message")]
    connection.send_result(msg["id"], {"services": targets})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_notification_targets",
        vol.Required("two_n_uuid"): str,
    }
)
@callback
def ws_get_notification_targets(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return the notification targets configured for a 2N user."""
    store = _store(hass)
    targets = store.get_notification_targets(msg["two_n_uuid"]) if store else []
    connection.send_result(msg["id"], {"targets": targets})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_notification_targets",
        vol.Required("two_n_uuid"): str,
        vol.Required("targets"): [str],
    }
)
@websocket_api.async_response
async def ws_set_notification_targets(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Persist notification targets for a 2N user."""
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    store = _store(hass)
    if store is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    await store.set_notification_targets(msg["two_n_uuid"], msg["targets"])
    connection.send_result(msg["id"], {"success": True})
