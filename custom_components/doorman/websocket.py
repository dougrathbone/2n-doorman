"""WebSocket API handlers for the Doorman sidebar panel."""
from __future__ import annotations

import re

import voluptuous as vol
from homeassistant.components import websocket_api
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv

from .api_client import DoormanApiError
from .const import DOMAIN
from .coordinator import DoormanCoordinator
from .ios_sounds import catalog_for_ws
from .sanitize import CARD, PIN_OR_CODE, sanitize_directory_user
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


def _registered_notify_targets(hass: HomeAssistant) -> set[str]:
    """Return the set of currently registered ``notify.*`` service targets."""
    return {
        f"notify.{name}"
        for name in hass.services.async_services().get("notify", {})
        if name not in ("notify", "send_message")
    }


def _validate_notify_targets(
    hass: HomeAssistant, targets: list[str]
) -> list[str] | str:
    """Return the validated target list, or an error message string.

    Every target must be a currently registered ``notify.*`` service so the
    store cannot accumulate dead or out-of-domain spam destinations.
    """
    allowed = _registered_notify_targets(hass)
    invalid = [t for t in targets if t not in allowed]
    if invalid:
        return (
            "Unknown notify target(s): "
            + ", ".join(repr(t) for t in invalid)
            + ". Choose from registered notify.* services."
        )
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered: list[str] = []
    for target in targets:
        if target not in seen:
            seen.add(target)
            ordered.append(target)
    return ordered


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
    websocket_api.async_register_command(hass, ws_create_user)
    websocket_api.async_register_command(hass, ws_update_user)
    websocket_api.async_register_command(hass, ws_delete_user)
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

    These commands expose the directory, access log, device details, and
    credential write paths, so they must not be reachable by non-admin HA
    users even though the WebSocket connection itself is authenticated.
    """
    if not connection.user.is_admin:
        connection.send_error(msg["id"], "unauthorized", "Admin access required")
        return False
    return True


def _validity_timestamp(value: object) -> int:
    """Convert a validated valid_from/valid_to value to a Unix timestamp.

    ``0`` clears the restriction (the 2N API represents "no restriction" as
    validFrom/validTo ``0``). A datetime becomes ``int(timestamp())``.
    """
    if isinstance(value, int):
        return value
    return int(value.timestamp())  # type: ignore[union-attr]


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
            **sanitize_directory_user(user),
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


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/create_user",
        vol.Optional("entry_id"): str,
        vol.Required("name"): cv.string,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("pin"): PIN_OR_CODE,
        vol.Optional("card"): CARD,
        vol.Optional("code"): PIN_OR_CODE,
        vol.Optional("valid_from"): cv.datetime,
        vol.Optional("valid_to"): cv.datetime,
    }
)
@websocket_api.async_response
async def ws_create_user(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Create a 2N directory user (admin panel path; mirrors doorman.create_user)."""
    if not _require_admin(connection, msg):
        return
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    user: dict = {"name": msg["name"]}
    if "enabled" in msg:
        user["enabled"] = msg["enabled"]
    if pin := msg.get("pin"):
        user["pin"] = pin
    if card := msg.get("card"):
        user["card"] = [card]
    if code := msg.get("code"):
        user["code"] = [code]
    if "valid_from" in msg:
        user["validFrom"] = _validity_timestamp(msg["valid_from"])
    if "valid_to" in msg:
        user["validTo"] = _validity_timestamp(msg["valid_to"])
    try:
        await coordinator.client.create_user(user)
    except DoormanApiError as err:
        connection.send_error(
            msg["id"], "2n_api_error", f"create_user failed on the 2N device: {err}"
        )
        return
    await coordinator.async_request_refresh()
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/update_user",
        vol.Optional("entry_id"): str,
        vol.Required("uuid"): cv.string,
        vol.Optional("name"): cv.string,
        vol.Optional("enabled"): cv.boolean,
        vol.Optional("pin"): PIN_OR_CODE,
        vol.Optional("card"): CARD,
        vol.Optional("code"): PIN_OR_CODE,
        # A datetime sets the restriction; 0 clears it.
        vol.Optional("valid_from"): vol.Any(cv.datetime, 0),
        vol.Optional("valid_to"): vol.Any(cv.datetime, 0),
    }
)
@websocket_api.async_response
async def ws_update_user(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Update a 2N directory user (admin panel path; mirrors doorman.update_user)."""
    if not _require_admin(connection, msg):
        return
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    user: dict = {"uuid": msg["uuid"]}
    if "name" in msg and msg["name"]:
        user["name"] = msg["name"]
    # An explicitly empty string clears the PIN, mirroring card/code below.
    if "pin" in msg:
        user["pin"] = msg["pin"]
    if "enabled" in msg:
        user["enabled"] = msg["enabled"]
    if "card" in msg:
        user["card"] = [msg["card"]] if msg["card"] else []
    if "code" in msg:
        user["code"] = [msg["code"]] if msg["code"] else []
    if "valid_from" in msg:
        user["validFrom"] = _validity_timestamp(msg["valid_from"])
    if "valid_to" in msg:
        user["validTo"] = _validity_timestamp(msg["valid_to"])
    try:
        await coordinator.client.update_user(user)
    except DoormanApiError as err:
        connection.send_error(
            msg["id"], "2n_api_error", f"update_user failed on the 2N device: {err}"
        )
        return
    await coordinator.async_request_refresh()
    connection.send_result(msg["id"], {"success": True})


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/delete_user",
        vol.Optional("entry_id"): str,
        vol.Required("uuid"): cv.string,
    }
)
@websocket_api.async_response
async def ws_delete_user(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict,
) -> None:
    """Delete a 2N directory user and drop local link/notify state."""
    if not _require_admin(connection, msg):
        return
    coordinator = _coordinator(hass, msg.get("entry_id"))
    if coordinator is None:
        connection.send_error(msg["id"], "not_configured", "Doorman is not configured")
        return

    try:
        await coordinator.client.delete_user(msg["uuid"])
    except DoormanApiError as err:
        connection.send_error(
            msg["id"], "2n_api_error", f"delete_user failed on the 2N device: {err}"
        )
        return
    store = _store(hass)
    if store:
        await store.unlink_user(msg["uuid"])
        await store.clear_notification_targets(msg["uuid"])
    await coordinator.async_request_refresh()
    connection.send_result(msg["id"], {"success": True})


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

    ha_user = await hass.auth.async_get_user(msg["ha_user_id"])
    if ha_user is None or ha_user.system_generated or not ha_user.is_active:
        connection.send_error(
            msg["id"],
            "unknown_user",
            f"Home Assistant user {msg['ha_user_id']!r} was not found",
        )
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

    validated = _validate_notify_targets(hass, msg["targets"])
    if isinstance(validated, str):
        connection.send_error(msg["id"], "invalid_target", validated)
        return

    await store.set_notification_targets(msg["two_n_uuid"], validated)
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

    settings = dict(msg["settings"])
    if "doorbell_targets" in settings:
        validated = _validate_notify_targets(hass, settings["doorbell_targets"])
        if isinstance(validated, str):
            connection.send_error(msg["id"], "invalid_target", validated)
            return
        settings["doorbell_targets"] = validated

    settings = await store.set_notification_settings(
        coordinator.config_entry.entry_id, settings
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

    # Ignore client-supplied title/message — Preview is admin-only, but fixed
    # copy still prevents a compromised admin session from phishing phones.
    title = "Doorman test"
    message = "This is a Doorman test notification."

    data: dict = {"tag": "doorman_test"}
    if msg.get("ios_sound"):
        data["push"] = {"sound": msg["ios_sound"]}
    if msg.get("android_channel"):
        data["channel"] = msg["android_channel"]

    try:
        await hass.services.async_call(
            "notify",
            service,
            {"title": title, "message": message, "data": data},
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
