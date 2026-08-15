"""Push notification dispatch for Doorman access events.

Listens on the HA bus for ``doorman_access`` events fired by the
coordinator. Two flows exist, each with independent sound/channel
presentation configured per-device from the sidebar panel:

* ``UserAuthenticated`` — dispatch per-user notifications to the notify
  targets configured for that specific 2N user, styled with the entry's
  ``access_sound_ios`` / ``access_channel_android`` settings.
* ``DoorbellPressed`` — dispatch per-device notifications to the
  targets under ``doorbell_targets``, styled with the entry's
  ``doorbell_sound_ios`` / ``doorbell_channel_android`` settings.

All per-flow settings are read from ``DoormanStore`` (keyed by config
entry_id), not from ``entry.options`` — see storage.py for why.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    CONF_ACCESS_CHANNEL_ANDROID,
    CONF_ACCESS_SOUND_IOS,
    CONF_DOORBELL_CHANNEL_ANDROID,
    CONF_DOORBELL_SOUND_IOS,
    CONF_DOORBELL_TARGETS,
    DOMAIN,
)
from .coordinator import DOORBELL_EVENT_TYPE

if TYPE_CHECKING:
    from .storage import DoormanStore

_LOGGER = logging.getLogger(__name__)


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
            _handle_doorbell_pressed(hass, event, entry)

    hass.data[f"{DOMAIN}_notifications_unsub"] = hass.bus.async_listen(
        f"{DOMAIN}_access", _on_access_event
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


def _build_data(
    tag: str,
    *,
    ios_sound: str = "",
    android_channel: str = "",
) -> dict:
    """Build the ``notify.data`` payload with per-platform presentation.

    Only fields explicitly configured are included — an empty string on
    either side means "use the Companion app default", so we don't
    overwrite that with an empty override.
    """
    data: dict = {"tag": tag}
    if ios_sound:
        data["push"] = {"sound": ios_sound}
    if android_channel:
        data["channel"] = android_channel
    return data


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


def _handle_doorbell_pressed(
    hass: HomeAssistant, event: Event, entry: ConfigEntry | None
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
    message = (
        f"{device_name}: someone rang the doorbell"
        if device_name
        else "Someone rang the doorbell"
    )
    ios_sound = settings.get(CONF_DOORBELL_SOUND_IOS, "") or ""
    android_channel = settings.get(CONF_DOORBELL_CHANNEL_ANDROID, "") or ""
    _dispatch(
        hass,
        targets,
        "Doorbell",
        message,
        data=_build_data(
            f"doorman_doorbell_{entry.entry_id}",
            ios_sound=ios_sound,
            android_channel=android_channel,
        ),
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
                {
                    "title": title,
                    "message": message,
                    "data": data,
                },
            )
        )
        _LOGGER.debug("Notification dispatched to %s: %s", service, message)
