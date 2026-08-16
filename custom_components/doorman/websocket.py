"""WebSocket API handlers for the Doorman sidebar panel."""
from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import DOMAIN
from .coordinator import DoormanCoordinator
from .ios_sounds import catalog_for_ws
from .storage import DoormanStore

# The panel's <select> uses this sentinel to mean "reveal a free-text field".
# It is UI-only: if it ever reached the backend it would be persisted as a
# sound filename and end up in ``data.push.sound``, where the Companion app
# would silently fall back to the default sound.
UI_CUSTOM_SENTINEL = "__custom__"

# 2N reports quick-dial / call buttons as "%N" in ``KeyPressed.params.key``;
# ordinary keypad presses are reported as bare characters ("5", "*", "#").
# Accepting a bare digit as the doorbell key would make every PIN keystroke
# ring the doorbell, so only the "%N" form is allowed.
_DOORBELL_KEY_RE = re.compile(r"^%\d{1,2}$")


def _doorbell_key_code(value: str) -> str:
    """Validate a doorbell key code.

    ``""`` is accepted and means "this device has no doorbell button" — the
    coordinator then fires no DoorbellPressed events at all. To restore the
    default, send the default value (``"%1"``) explicitly; there is
    deliberately no magic "reset" value, so the meaning of every accepted
    input is unambiguous.
    """
    if value == "":
        return value
    if not _DOORBELL_KEY_RE.match(value):
        raise vol.Invalid(
            "doorbell_key_code must be a 2N quick-dial code such as '%1', "
            f"or '' to disable the doorbell — got {value!r}"
        )
    return value


def _presentation_value(value: str) -> str:
    """Reject the panel's UI-only 'custom' sentinel from a sound/channel field."""
    if value == UI_CUSTOM_SENTINEL:
        raise vol.Invalid(
            f"{UI_CUSTOM_SENTINEL!r} is a UI placeholder, not a sound or channel name"
        )
    return value


_PRESENTATION = vol.All(str, _presentation_value)


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
    websocket_api.async_register_command(hass, ws_set_notification_targets)
    websocket_api.async_register_command(hass, ws_get_notification_settings)
    websocket_api.async_register_command(hass, ws_set_notification_settings)
    websocket_api.async_register_command(hass, ws_send_test_notification)
    websocket_api.async_register_command(hass, ws_subscribe_events)


def _coordinator(hass: HomeAssistant, entry_id: str | None = None) -> DoormanCoordinator | None:
    entries = hass.data.get(DOMAIN, {})
    if entry_id:
        return entries.get(entry_id)
    if len(entries) == 1:
        return next(iter(entries.values()))
    # Zero entries, or multiple entries without an explicit entry_id — the
    # caller maps None to a not_configured error. Guessing the first entry
    # would silently show the wrong device in a multi-device install.
    return None


def _store(hass: HomeAssistant) -> DoormanStore | None:
    return hass.data.get(f"{DOMAIN}_store")


def _require_admin(connection: websocket_api.ActiveConnection, msg: dict) -> bool:
    """Return True if the caller is an admin; otherwise send an error and return False.

    These commands expose door credentials (PINs, cards, codes), the access log
    and device details, so they must not be reachable by non-admin HA users even
    though the WebSocket connection itself is authenticated.
    """
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return False
    return True


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
    if not _require_admin(connection, msg):
        return
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
    if not _require_admin(connection, msg):
        return
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
    if not _require_admin(connection, msg):
        return
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
    if not _require_admin(connection, msg):
        return
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
    if not _require_admin(connection, msg):
        return
    notify_services = list(hass.services.async_services().get("notify", {}).keys())
    targets = [f"notify.{s}" for s in notify_services if s not in ("notify", "send_message")]
    connection.send_result(msg["id"], {"services": targets})


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


# ------------------------------------------------------------------ #
# Per-entry notification settings (Notifications tab)                  #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/get_notification_settings",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_get_notification_settings(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Return notification settings + the iOS sound catalog for the panel.

    The catalog is returned alongside the settings so a single round-trip
    populates the entire Notifications tab (dropdown options + current
    values). A separate ``list_notify_services`` call still exists for
    the per-user Users tab.
    """
    if not _require_admin(connection, msg):
        return
    coordinator = _coordinator(hass, msg.get("entry_id"))
    store = _store(hass)
    if coordinator is None or store is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    entry = coordinator.config_entry
    notify_services = sorted(
        f"notify.{s}"
        for s in hass.services.async_services().get("notify", {})
        if s not in ("notify", "send_message")
    )
    connection.send_result(
        msg["id"],
        {
            "device_name": entry.title,
            # The DoormanStore keys are the wire keys (see const.py), and the
            # store fills in defaults, so the stored dict is the payload.
            "settings": store.get_notification_settings(entry.entry_id),
            "ios_sound_catalog": catalog_for_ws(),
            "notify_services": notify_services,
        },
    )


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/set_notification_settings",
        vol.Optional("entry_id"): str,
        vol.Required("settings"): {
            vol.Optional("access_sound_ios"): _PRESENTATION,
            vol.Optional("access_channel_android"): _PRESENTATION,
            vol.Optional("doorbell_sound_ios"): _PRESENTATION,
            vol.Optional("doorbell_channel_android"): _PRESENTATION,
            vol.Optional("doorbell_key_code"): vol.All(str, _doorbell_key_code),
            vol.Optional("doorbell_targets"): [str],
        },
    }
)
@websocket_api.async_response
async def ws_set_notification_settings(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Persist all notification-related settings for a Doorman entry.

    Written to ``DoormanStore`` keyed by entry_id, deliberately not to
    ``entry.options``:

    * The options flow only renders ``poll_interval`` and replaces the whole
      options dict on submit, so anything else stored there is destroyed the
      first time someone opens Configure.
    * Writing options fires the entry's update listener, reloading the entry.
      That drops the 2N log subscription (a fresh one starts empty, with no
      watermark), so every event in the reload window is lost — while the user
      is standing on the panel that triggered the save.

    No reload is needed: the coordinator re-reads the doorbell key from the
    store on every log batch, and notifications.py reads sounds/channels per
    event, so a saved change applies to the very next event.
    """
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return
    coordinator = _coordinator(hass, msg.get("entry_id"))
    store = _store(hass)
    if coordinator is None or store is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    settings = await store.set_notification_settings(
        coordinator.config_entry.entry_id, msg["settings"]
    )
    connection.send_result(msg["id"], {"settings": settings})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/send_test_notification",
        vol.Required("target"): str,
        vol.Required("title"): str,
        vol.Required("message"): str,
        vol.Optional("ios_sound", default=""): _PRESENTATION,
        vol.Optional("android_channel", default=""): _PRESENTATION,
    }
)
@websocket_api.async_response
async def ws_send_test_notification(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Fire a single notify.* call for the Preview button.

    Uses the same payload shape as the real dispatch (``data.push.sound``
    on iOS, ``data.channel`` on Android) so what the user hears in
    preview matches what they'll hear live.

    The call is ``blocking=True`` on purpose. The whole point of Preview is to
    report whether the notification actually went out, so a failure from the
    Companion handler (expired push token, unregistered device, bad channel)
    must reach the panel as an error rather than a green "sent" toast.
    """
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return

    target: str = msg["target"]
    service = target.removeprefix("notify.")
    if not hass.services.has_service("notify", service):
        connection.send_error(
            msg["id"],
            "unknown_target",
            f"Notify target {target!r} is not registered",
        )
        return

    data: dict = {"tag": "doorman_test"}
    if msg.get("ios_sound"):
        data["push"] = {"sound": msg["ios_sound"]}
    if msg.get("android_channel"):
        data["channel"] = msg["android_channel"]

    try:
        await hass.services.async_call(
            "notify",
            service,
            {"title": msg["title"], "message": msg["message"], "data": data},
            blocking=True,
        )
    except HomeAssistantError as err:
        # Expected failure mode (the notify platform rejected the payload or
        # the device is gone): report it cleanly instead of letting the
        # generic handler log a traceback.
        connection.send_error(msg["id"], "notify_failed", str(err))
        return

    connection.send_result(msg["id"], {"success": True})


# ------------------------------------------------------------------ #
# Live event subscription                                              #
# ------------------------------------------------------------------ #

@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/subscribe_events",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_subscribe_events(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Subscribe the panel to live ``doorman_access`` events for one device.

    Each matching bus event is pushed to the subscriber as a WS event with
    the same shape as the log-tab rows ({event_type, params, utc_time}),
    so the access log updates live instead of only on manual refresh.
    """
    if not _require_admin(connection, msg):
        return
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(
            msg["id"],
            "not_configured",
            "No Doorman device is configured, or entry_id is required for multi-device installs",
        )
        return
    entry_id = coordinator.config_entry.entry_id

    @callback
    def _forward(event: Event) -> None:
        if event.data.get("entry_id") != entry_id:
            return
        connection.send_event(
            msg["id"],
            {
                "entry_id": entry_id,
                "event_type": event.data.get("event_type"),
                "params": event.data.get("params", {}),
                "utc_time": event.data.get("utc_time"),
            },
        )

    # Dropping the entry in .subscriptions makes HA cancel it on disconnect.
    connection.subscriptions[msg["id"]] = hass.bus.async_listen(
        f"{DOMAIN}_access", _forward
    )
    connection.send_result(msg["id"], {"entry_id": entry_id})
