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
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    ACTION_ANSWER_PREFIX,
    ACTION_TTL_SECONDS,
    ACTION_UNLOCK_PREFIX,
    CONF_ACCESS_CHANNEL_ANDROID,
    CONF_ACCESS_SOUND_IOS,
    CONF_DOORBELL_ANSWER_ACTION,
    CONF_DOORBELL_ATTACH_CAMERA,
    CONF_DOORBELL_CHANNEL_ANDROID,
    CONF_DOORBELL_NOTIFY_ON_CALL_RINGING,
    CONF_DOORBELL_SOUND_IOS,
    CONF_DOORBELL_TARGETS,
    CONF_DOORBELL_TIME_SENSITIVE,
    CONF_DOORBELL_UNLOCK_ACCESS_POINT_ID,
    CONF_DOORBELL_UNLOCK_ACTION,
    CONF_DOORBELL_UNLOCK_USER_UUID,
    DOMAIN,
    DOORBELL_CALL_DEBOUNCE_SECONDS,
)
from .coordinator import CALL_RINGING_EVENT_TYPE, DOORBELL_EVENT_TYPE
from .helpers import pinned_entity_id

if TYPE_CHECKING:
    from .storage import DoormanStore

_LOGGER = logging.getLogger(__name__)

# Companion fires this when the user taps a notification action button.
_MOBILE_APP_ACTION_EVENT = "mobile_app_notification_action"
_NOTIFY_AT_KEY = f"{DOMAIN}_doorbell_notify_at"


@callback
def async_setup_notifications(hass: HomeAssistant) -> None:
    """Register the access-event listener that dispatches push notifications."""
    if hass.data.get(f"{DOMAIN}_notifications_registered"):
        return
    hass.data[f"{DOMAIN}_notifications_registered"] = True

    @callback
    def _on_access_event(event: Event) -> None:
        event_type: str = event.data.get("event_type", "")
        if event_type not in (
            "UserAuthenticated",
            DOORBELL_EVENT_TYPE,
            CALL_RINGING_EVENT_TYPE,
        ):
            return
        entry = _lookup_entry(hass, event.data.get("entry_id"))

        if event_type == "UserAuthenticated":
            _handle_user_authenticated(hass, event, entry)
        elif event_type == DOORBELL_EVENT_TYPE:
            _handle_doorbell_notify(hass, event, entry, kind="doorbell")
        else:
            _handle_call_ringing_notify(hass, event, entry)

    @callback
    def _on_notification_action(event: Event) -> None:
        action = event.data.get("action") or ""
        if not (
            action.startswith(ACTION_UNLOCK_PREFIX)
            or action.startswith(ACTION_ANSWER_PREFIX)
        ):
            return
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


def camera_entity_id(hass: HomeAssistant, entry: ConfigEntry) -> str | None:
    """Return the Doorman camera entity id when the device has a camera."""
    coordinator = _coordinator_for(hass, entry.entry_id)
    if coordinator is None or not coordinator.camera_caps:
        return None
    entity_id = pinned_entity_id("camera", "camera", coordinator, entry)
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unavailable", "unknown"):
        return None
    return entity_id


def _doorbell_tag(entry_id: str) -> str:
    return f"doorman_doorbell_{entry_id}"


def _mark_doorbell_notified(hass: HomeAssistant, entry_id: str) -> None:
    hass.data.setdefault(_NOTIFY_AT_KEY, {})[entry_id] = time.time()


def _call_notify_suppressed(hass: HomeAssistant, entry_id: str) -> bool:
    """True when a DoorbellPressed notify just fired for this entry."""
    at = hass.data.get(_NOTIFY_AT_KEY, {}).get(entry_id)
    return at is not None and (time.time() - at) < DOORBELL_CALL_DEBOUNCE_SECONDS


def _build_data(
    tag: str,
    *,
    ios_sound: str = "",
    android_channel: str = "",
    camera_entity_id: str | None = None,
    actions: list[dict[str, Any]] | None = None,
    time_sensitive: bool = False,
) -> dict[str, Any]:
    """Build the ``notify.data`` payload with per-platform presentation.

    Only fields explicitly configured are included — an empty string on
    either side means "use the Companion app default", so we don't
    overwrite that with an empty override.
    """
    data: dict[str, Any] = {"tag": tag}
    push: dict[str, Any] = {}
    if ios_sound:
        push["sound"] = ios_sound
    if time_sensitive:
        push["interruption-level"] = "time-sensitive"
        data["ttl"] = 0
        data["priority"] = "high"
    if push:
        data["push"] = push
    if android_channel:
        data["channel"] = android_channel
    if camera_entity_id:
        # JPEG still via camera_proxy — works on iOS and Android. Do not set
        # ``entity_id``: Companion treats that as an iOS dynamic *stream*
        # attachment, and DoormanCamera has no stream.
        data["image"] = f"/api/camera_proxy/{camera_entity_id}"
    if actions:
        data["actions"] = actions
        # Android auto-dismiss; pairs with the issued-at check on Unlock.
        data["timeout"] = ACTION_TTL_SECONDS
    return data


def build_test_doorbell_data(
    hass: HomeAssistant, entry: ConfigEntry, settings: dict
) -> dict[str, Any]:
    """Build a Preview payload mirroring live doorbell extras.

    Action buttons use a no-op action id so tapping them cannot unlock or
    answer — Preview is for layout/sound/snapshot verification only.
    """
    camera = None
    if settings.get(CONF_DOORBELL_ATTACH_CAMERA, True):
        camera = camera_entity_id(hass, entry)
    return _build_data(
        "doorman_test",
        ios_sound=settings.get(CONF_DOORBELL_SOUND_IOS, "") or "",
        android_channel=settings.get(CONF_DOORBELL_CHANNEL_ANDROID, "") or "",
        camera_entity_id=camera,
        actions=_doorbell_actions(
            hass, entry, settings, kind="call", preview=True
        )
        or None,
        time_sensitive=bool(settings.get(CONF_DOORBELL_TIME_SENSITIVE)),
    )


def _doorbell_actions(
    hass: HomeAssistant,
    entry: ConfigEntry,
    settings: dict,
    *,
    kind: str,
    preview: bool = False,
    call_session: int | None = None,
) -> list[dict[str, Any]]:
    """Build Companion action buttons for Unlock / Answer."""
    actions: list[dict[str, Any]] = []
    issued = int(time.time())
    if settings.get(CONF_DOORBELL_UNLOCK_ACTION):
        actions.append(
            {
                "action": (
                    "DOORMAN_PREVIEW_NOOP"
                    if preview
                    else f"{ACTION_UNLOCK_PREFIX}{entry.entry_id}|{issued}"
                ),
                "title": "Unlock (preview)" if preview else "Unlock",
                "authenticationRequired": True,
                "destructive": True,
            }
        )
    if kind == "call" and settings.get(CONF_DOORBELL_ANSWER_ACTION):
        coordinator = _coordinator_for(hass, entry.entry_id)
        if coordinator is not None and coordinator.call_status_available:
            if preview:
                answer_action = "DOORMAN_PREVIEW_NOOP"
            elif call_session is not None:
                answer_action = (
                    f"{ACTION_ANSWER_PREFIX}{entry.entry_id}|{issued}|{call_session}"
                )
            else:
                # No session on the event — fall back to "first ringing" at tap.
                answer_action = f"{ACTION_ANSWER_PREFIX}{entry.entry_id}|{issued}"
            actions.append(
                {
                    "action": answer_action,
                    "title": "Answer (preview)" if preview else "Answer",
                }
            )
    return actions


def _parse_unlock_payload(action: str) -> tuple[str, int] | None:
    """Split ``DOORMAN_UNLOCK|{entry_id}|{issued_at}``."""
    remainder = action.removeprefix(ACTION_UNLOCK_PREFIX)
    entry_id, sep, issued_raw = remainder.partition("|")
    if not sep or not entry_id or not issued_raw or "|" in issued_raw:
        return None
    try:
        issued_at = int(issued_raw)
    except ValueError:
        return None
    return entry_id, issued_at


def _parse_answer_payload(action: str) -> tuple[str, int, int | None] | None:
    """Split ``DOORMAN_ANSWER|{entry_id}|{issued_at}[|{session}]``."""
    remainder = action.removeprefix(ACTION_ANSWER_PREFIX)
    entry_id, sep, rest = remainder.partition("|")
    if not sep or not entry_id or not rest:
        return None
    issued_raw, sep2, session_raw = rest.partition("|")
    try:
        issued_at = int(issued_raw)
    except ValueError:
        return None
    if not sep2:
        return entry_id, issued_at, None
    try:
        return entry_id, issued_at, int(session_raw)
    except ValueError:
        return None


def _action_expired(issued_at: int) -> bool:
    return (time.time() - issued_at) > ACTION_TTL_SECONDS


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


def _handle_call_ringing_notify(
    hass: HomeAssistant, event: Event, entry: ConfigEntry | None
) -> None:
    settings = _settings_for(hass, entry)
    if not settings.get(CONF_DOORBELL_NOTIFY_ON_CALL_RINGING):
        return
    if entry is not None and _call_notify_suppressed(hass, entry.entry_id):
        _LOGGER.debug(
            "Skipping CallRinging notify for %s — doorbell already notified",
            entry.entry_id,
        )
        return

    params: dict = event.data.get("params") or {}
    direction = params.get("direction")
    # Outgoing ringing is the common doorbell-dial path (no softphone Answer).
    # Incoming (or unknown) gets intercom-call wording plus Answer when enabled.
    if direction == "outgoing":
        _handle_doorbell_notify(hass, event, entry, kind="doorbell")
    else:
        _handle_doorbell_notify(hass, event, entry, kind="call")


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

    camera = None
    if settings.get(CONF_DOORBELL_ATTACH_CAMERA, True):
        camera = camera_entity_id(hass, entry)

    call_session: int | None = None
    if kind == "call":
        raw_session = (event.data.get("params") or {}).get("session")
        if raw_session is not None:
            try:
                call_session = int(raw_session)
            except (TypeError, ValueError):
                call_session = None

    _dispatch(
        hass,
        targets,
        title,
        message,
        data=_build_data(
            _doorbell_tag(entry.entry_id),
            ios_sound=settings.get(CONF_DOORBELL_SOUND_IOS, "") or "",
            android_channel=settings.get(CONF_DOORBELL_CHANNEL_ANDROID, "") or "",
            camera_entity_id=camera,
            actions=_doorbell_actions(
                hass,
                entry,
                settings,
                kind=kind,
                call_session=call_session,
            )
            or None,
            time_sensitive=bool(settings.get(CONF_DOORBELL_TIME_SENSITIVE)),
        ),
    )
    if kind == "doorbell":
        _mark_doorbell_notified(hass, entry.entry_id)


async def _async_handle_notification_action(hass: HomeAssistant, event: Event) -> None:
    """Run Unlock / Answer when the user taps a Companion notification button."""
    action = event.data.get("action") or ""
    user_id = event.context.user_id if event.context else None
    if action.startswith(ACTION_UNLOCK_PREFIX):
        parsed = _parse_unlock_payload(action)
        if parsed is None:
            _LOGGER.info("Doorman Unlock ignored — malformed or legacy action id")
            return
        entry_id, issued_at = parsed
        if _action_expired(issued_at):
            _LOGGER.info("Doorman Unlock ignored — action expired for %s", entry_id)
            return
        await _async_unlock(hass, entry_id, user_id=user_id)
    elif action.startswith(ACTION_ANSWER_PREFIX):
        parsed = _parse_answer_payload(action)
        if parsed is None:
            _LOGGER.info("Doorman Answer ignored — malformed or legacy action id")
            return
        entry_id, issued_at, session = parsed
        if _action_expired(issued_at):
            _LOGGER.info("Doorman Answer ignored — action expired for %s", entry_id)
            return
        await _async_answer(hass, entry_id, session=session, user_id=user_id)


async def _async_unlock(
    hass: HomeAssistant, entry_id: str, *, user_id: str | None = None
) -> None:
    entry = _lookup_entry(hass, entry_id)
    coordinator = _coordinator_for(hass, entry_id)
    if entry is None or coordinator is None:
        _LOGGER.warning("Doorman Unlock: unknown entry %s", entry_id)
        return

    settings = _settings_for(hass, entry)
    if not settings.get(CONF_DOORBELL_UNLOCK_ACTION):
        _LOGGER.debug("Doorman Unlock ignored — action disabled for %s", entry_id)
        return

    raw_ap = settings.get(CONF_DOORBELL_UNLOCK_ACCESS_POINT_ID, 1)
    try:
        access_point_id = int(raw_ap)
    except (TypeError, ValueError):
        _LOGGER.warning(
            "Doorman Unlock: invalid access point %r on %s", raw_ap, entry.title
        )
        _notify_action_failure(
            hass, entry, settings, f"Unlock failed on {entry.title}: bad access point"
        )
        return
    if access_point_id < 1:
        _LOGGER.warning(
            "Doorman Unlock: access point %s out of range on %s",
            access_point_id,
            entry.title,
        )
        _notify_action_failure(
            hass, entry, settings, f"Unlock failed on {entry.title}: bad access point"
        )
        return

    user_uuid = (settings.get(CONF_DOORBELL_UNLOCK_USER_UUID) or "").strip() or None
    try:
        await coordinator.client.grant_access(access_point_id, user_uuid)
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Doorman Unlock failed on %s: %s", entry.title, err)
        _notify_action_failure(
            hass, entry, settings, f"Unlock failed on {entry.title}"
        )
        return
    _LOGGER.info(
        "Doorman Unlock via notification on %s (access point %s, ha_user=%s)",
        entry.title,
        access_point_id,
        user_id,
    )
    _clear_doorbell_notifications(hass, entry, settings)


async def _async_answer(
    hass: HomeAssistant,
    entry_id: str,
    *,
    session: int | None = None,
    user_id: str | None = None,
) -> None:
    entry = _lookup_entry(hass, entry_id)
    coordinator = _coordinator_for(hass, entry_id)
    if entry is None or coordinator is None:
        _LOGGER.warning("Doorman Answer: unknown entry %s", entry_id)
        return

    settings = _settings_for(hass, entry)
    if not settings.get(CONF_DOORBELL_ANSWER_ACTION):
        _LOGGER.debug("Doorman Answer ignored — action disabled for %s", entry_id)
        return

    try:
        if session is not None:
            await coordinator.client.answer_call(session)
            answered = True
        else:
            answered = await coordinator.client.answer_ringing_call()
    except Exception as err:  # noqa: BLE001
        _LOGGER.error("Doorman Answer failed on %s: %s", entry.title, err)
        _notify_action_failure(
            hass, entry, settings, f"Answer failed on {entry.title}"
        )
        return
    if answered:
        _LOGGER.info(
            "Doorman Answer via notification on %s (session=%s, ha_user=%s)",
            entry.title,
            session,
            user_id,
        )
        _clear_doorbell_notifications(hass, entry, settings)
    else:
        _LOGGER.info(
            "Doorman Answer: no ringing incoming call on %s", entry.title
        )
        _notify_action_failure(
            hass,
            entry,
            settings,
            f"No ringing incoming call on {entry.title}",
        )


def _notify_action_failure(
    hass: HomeAssistant, entry: ConfigEntry, settings: dict, message: str
) -> None:
    """Replace the actionable doorbell push with a short failure notice."""
    targets = settings.get(CONF_DOORBELL_TARGETS) or []
    if not targets:
        return
    _dispatch(
        hass,
        targets,
        "Doorman",
        message,
        data={"tag": _doorbell_tag(entry.entry_id)},
    )


def _clear_doorbell_notifications(
    hass: HomeAssistant, entry: ConfigEntry, settings: dict
) -> None:
    """Dismiss the actionable doorbell push on every configured phone."""
    _dispatch(
        hass,
        settings.get(CONF_DOORBELL_TARGETS) or [],
        "",
        "clear_notification",
        data={"tag": _doorbell_tag(entry.entry_id)},
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
        payload: dict[str, Any] = {"message": message, "data": data}
        if title:
            payload["title"] = title
        hass.async_create_task(
            hass.services.async_call(
                "notify",
                service,
                payload,
                blocking=False,
            )
        )
