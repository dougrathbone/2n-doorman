"""Push notification dispatch for Doorman access events.

Listens on the HA bus for ``doorman_access`` events fired by the
coordinator. Two flows exist, each with independent sound/channel
presentation configured per-device from the sidebar panel:

* ``UserAuthenticated`` — dispatch per-user notifications to the notify
  targets configured for that specific 2N user, styled with the entry's
  ``access_sound_ios`` / ``access_channel_android`` settings.
* ``DoorbellPressed`` (and optionally ``CallRinging``) — dispatch
  per-device notifications to ``doorbell_targets``, optionally with a
  camera snapshot and Companion Unlock / Answer actions.

All per-flow settings are read from ``DoormanStore`` (keyed by config
entry_id), not from ``entry.options`` — see storage.py for why.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError

from .const import (
    ACTION_ANSWER_PREFIX,
    ACTION_UNLOCK_PREFIX,
    CONF_ACCESS_CHANNEL_ANDROID,
    CONF_ACCESS_SOUND_IOS,
    CONF_DOORBELL_ANSWER_ACTION,
    CONF_DOORBELL_ATTACH_CAMERA,
    CONF_DOORBELL_CHANNEL_ANDROID,
    CONF_DOORBELL_NOTIFY_ON_CALL_RINGING,
    CONF_DOORBELL_SOUND_IOS,
    CONF_DOORBELL_TARGETS,
    CONF_DOORBELL_UNLOCK_ACCESS_POINT_ID,
    CONF_DOORBELL_UNLOCK_ACTION,
    CONF_DOORBELL_UNLOCK_USER_UUID,
    DOMAIN,
)
from .coordinator import CALL_RINGING_EVENT_TYPE, DOORBELL_EVENT_TYPE
from .helpers import pinned_entity_id

if TYPE_CHECKING:
    from .storage import DoormanStore

_LOGGER = logging.getLogger(__name__)

# Companion fires this when the user taps a notification action button.
_MOBILE_APP_ACTION_EVENT = "mobile_app_notification_action"


@callback
def async_setup_notifications(hass: HomeAssistant) -> None:
    """Register the access-event listener that dispatches push notifications."""
    if hass.data.get(f"{DOMAIN}_notifications_registered"):
        return
    hass.data[f"{DOMAIN}_notifications_registered"] = True

    @callback
    def _on_access_event(event: Event) -> None:
        event_type: str = event.data.get("event_type", "")
        entry = _lookup_entry(hass, event.data.get("entry_id"))

        if event_type == "UserAuthenticated":
            _handle_user_authenticated(hass, event, entry)
        elif event_type == DOORBELL_EVENT_TYPE:
            _handle_doorbell_notify(hass, event, entry, kind="doorbell")
        elif event_type == CALL_RINGING_EVENT_TYPE:
            settings = _settings_for(hass, entry)
            if settings.get(CONF_DOORBELL_NOTIFY_ON_CALL_RINGING):
                _handle_doorbell_notify(hass, event, entry, kind="call")

    @callback
    def _on_notification_action(event: Event) -> None:
        hass.async_create_task(_async_handle_notification_action(hass, event))

    hass.data[f"{DOMAIN}_notifications_unsub"] = hass.bus.async_listen(
        f"{DOMAIN}_access", _on_access_event
    )
    hass.data[f"{DOMAIN}_notification_action_unsub"] = hass.bus.async_listen(
        _MOBILE_APP_ACTION_EVENT, _on_notification_action
    )


def _lookup_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry | None:
    if not entry_id:
        return None
    return hass.config_entries.async_get_entry(entry_id)


def _get_store(hass: HomeAssistant) -> DoormanStore | None:
    return hass.data.get(f"{DOMAIN}_store")


def _settings_for(hass: HomeAssistant, entry: ConfigEntry | None) -> dict:
    """Return an entry's notification settings, or {} when unavailable.

    Read fresh on every event so a panel save takes effect immediately —
    nothing here is cached and no reload is involved.
    """
    store = _get_store(hass)
    if store is None or entry is None:
        return {}
    return store.get_notification_settings(entry.entry_id)


def _coordinator_for(hass: HomeAssistant, entry_id: str):
    return hass.data.get(DOMAIN, {}).get(entry_id)


def _camera_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the Doorman camera entity id when the device has a camera."""
    coordinator = _coordinator_for(hass, entry.entry_id)
    if coordinator is None or not coordinator.camera_caps:
        return None
    entity_id = pinned_entity_id("camera", "camera", coordinator, entry)
    if hass.states.get(entity_id) is None:
        return None
    return entity_id


def _build_data(
    tag: str,
    *,
    ios_sound: str = "",
    android_channel: str = "",
    camera_entity_id: str | None = None,
    actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Build the ``notify.data`` payload with per-platform presentation.

    Only fields explicitly configured are included — an empty string on
    either side means "use the Companion app default", so we don't
    overwrite that with an empty override.
    """
    data: dict[str, Any] = {"tag": tag}
    if ios_sound:
        data["push"] = {"sound": ios_sound}
    if android_channel:
        data["channel"] = android_channel
    if camera_entity_id:
        # Companion uses entity_id for a live snapshot; image is the
        # camera_proxy URL many clients also understand.
        data["entity_id"] = camera_entity_id
        data["image"] = f"/api/camera_proxy/{camera_entity_id}"
    if actions:
        data["actions"] = actions
    return data


def _doorbell_actions(entry: ConfigEntry, settings: dict) -> list[dict[str, str]]:
    """Build Companion action buttons for Unlock / Answer."""
    actions: list[dict[str, str]] = []
    if settings.get(CONF_DOORBELL_UNLOCK_ACTION):
        actions.append(
            {
                "action": f"{ACTION_UNLOCK_PREFIX}{entry.entry_id}",
                "title": "Unlock",
            }
        )
    if settings.get(CONF_DOORBELL_ANSWER_ACTION):
        actions.append(
            {
                "action": f"{ACTION_ANSWER_PREFIX}{entry.entry_id}",
                "title": "Answer",
            }
        )
    return actions


def _handle_user_authenticated(
    hass: HomeAssistant, event: Event, entry: ConfigEntry | None
) -> None:
    store = _get_store(hass)
    if store is None:
        return

    params: dict = event.data.get("params", {})
    # 2N places identifiers flat on params (name/uuid), not under a
    # nested "user" object.
    two_n_uuid: str | None = params.get("uuid")
    user_name: str = params.get("name") or "Someone"

    if not two_n_uuid:
        _LOGGER.debug("Access event has no user UUID — skipping notifications")
        return

    targets = store.get_notification_targets(two_n_uuid)
    if not targets:
        return

    device_name = entry.title if entry is not None else None
    message = (
        f"{user_name} opened {device_name}"
        if device_name
        else f"{user_name} opened the door"
    )
    settings = _settings_for(hass, entry)
    ios_sound = settings.get(CONF_ACCESS_SOUND_IOS, "") or ""
    android_channel = settings.get(CONF_ACCESS_CHANNEL_ANDROID, "") or ""
    _dispatch(
        hass,
        targets,
        "Doorman",
        message,
        data=_build_data(
            f"doorman_{two_n_uuid}",
            ios_sound=ios_sound,
            android_channel=android_channel,
        ),
    )


def _handle_doorbell_notify(
    hass: HomeAssistant,
    event: Event,
    entry: ConfigEntry | None,
    *,
    kind: str,
) -> None:
    if entry is None:
        # Doorbell targets are stored per config entry — without an entry
        # there's nowhere to look up who to notify.
        _LOGGER.debug("Doorbell event has no config entry — skipping notifications")
        return

    settings = _settings_for(hass, entry)
    targets = settings.get(CONF_DOORBELL_TARGETS) or []
    if not targets:
        return

    device_name = entry.title
    if kind == "call":
        title = "Intercom call"
        message = (
            f"{device_name}: incoming call"
            if device_name
            else "Incoming intercom call"
        )
    else:
        title = "Doorbell"
        message = (
            f"{device_name}: someone rang the doorbell"
            if device_name
            else "Someone rang the doorbell"
        )

    ios_sound = settings.get(CONF_DOORBELL_SOUND_IOS, "") or ""
    android_channel = settings.get(CONF_DOORBELL_CHANNEL_ANDROID, "") or ""
    camera_entity_id = None
    if settings.get(CONF_DOORBELL_ATTACH_CAMERA, True):
        camera_entity_id = _camera_entity_id(hass, entry)

    _dispatch(
        hass,
        targets,
        title,
        message,
        data=_build_data(
            f"doorman_doorbell_{entry.entry_id}",
            ios_sound=ios_sound,
            android_channel=android_channel,
            camera_entity_id=camera_entity_id,
            actions=_doorbell_actions(entry, settings) or None,
        ),
    )


async def _async_handle_notification_action(hass: HomeAssistant, event: Event) -> None:
    """Run Unlock / Answer when the user taps a Companion notification button."""
    action = event.data.get("action") or ""
    if action.startswith(ACTION_UNLOCK_PREFIX):
        entry_id = action.removeprefix(ACTION_UNLOCK_PREFIX)
        await _async_unlock(hass, entry_id)
    elif action.startswith(ACTION_ANSWER_PREFIX):
        entry_id = action.removeprefix(ACTION_ANSWER_PREFIX)
        await _async_answer(hass, entry_id)


async def _async_unlock(hass: HomeAssistant, entry_id: str) -> None:
    entry = _lookup_entry(hass, entry_id)
    coordinator = _coordinator_for(hass, entry_id)
    if entry is None or coordinator is None:
        _LOGGER.warning("Doorman Unlock: unknown entry %s", entry_id)
        return

    settings = _settings_for(hass, entry)
    if not settings.get(CONF_DOORBELL_UNLOCK_ACTION):
        _LOGGER.debug("Doorman Unlock ignored — action disabled for %s", entry_id)
        return

    access_point_id = int(settings.get(CONF_DOORBELL_UNLOCK_ACCESS_POINT_ID) or 1)
    user_uuid = (settings.get(CONF_DOORBELL_UNLOCK_USER_UUID) or "").strip() or None
    try:
        await coordinator.client.grant_access(access_point_id, user_uuid)
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Doorman Unlock failed on %s: %s", entry.title, err)
        return
    _LOGGER.info(
        "Doorman Unlock via notification on %s (access point %s)",
        entry.title,
        access_point_id,
    )


async def _async_answer(hass: HomeAssistant, entry_id: str) -> None:
    entry = _lookup_entry(hass, entry_id)
    coordinator = _coordinator_for(hass, entry_id)
    if entry is None or coordinator is None:
        _LOGGER.warning("Doorman Answer: unknown entry %s", entry_id)
        return

    settings = _settings_for(hass, entry)
    if not settings.get(CONF_DOORBELL_ANSWER_ACTION, True):
        _LOGGER.debug("Doorman Answer ignored — action disabled for %s", entry_id)
        return

    try:
        answered = await coordinator.client.answer_ringing_call()
    except (HomeAssistantError, Exception) as err:  # noqa: BLE001
        _LOGGER.error("Doorman Answer failed on %s: %s", entry.title, err)
        return
    if answered:
        _LOGGER.info("Doorman Answer via notification on %s", entry.title)
    else:
        _LOGGER.info(
            "Doorman Answer: no ringing incoming call on %s", entry.title
        )


def _dispatch(
    hass: HomeAssistant,
    targets: Iterable[str],
    title: str,
    message: str,
    *,
    data: dict,
) -> None:
    for target in targets:
        # Stored as "notify.service_name"; strip the domain prefix for the call
        service = target.removeprefix("notify.")
        if not hass.services.has_service("notify", service):
            # Target was removed (e.g. the mobile app was uninstalled) —
            # skip it instead of spawning a task that raises.
            _LOGGER.warning(
                "Doorman: notification target %s is not registered — skipping",
                target,
            )
            continue
        hass.async_create_task(
            hass.services.async_call(
                "notify",
                service,
                {"title": title, "message": message, "data": data},
                blocking=False,
            )
        )
