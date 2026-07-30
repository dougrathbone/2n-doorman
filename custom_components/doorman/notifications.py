"""Push notification dispatch for Doorman access events.

Listens on the HA bus for ``doorman_access`` events fired by the
coordinator.  When a user successfully authenticates and that user has
notification targets configured, a ``notify.*`` service call is made for
each target.
"""
from __future__ import annotations

import logging

from homeassistant.core import Event, HomeAssistant, callback

from .const import (
    CONF_NOTIFICATION_CHANNEL_ANDROID,
    CONF_NOTIFICATION_SOUND_IOS,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

# Only fire push notifications for successful entry — not rejected attempts
_NOTIFY_ON = frozenset({"UserAuthenticated"})


@callback
def async_setup_notifications(hass: HomeAssistant) -> None:
    """Register the access-event listener that dispatches push notifications."""
    if hass.data.get(f"{DOMAIN}_notifications_registered"):
        return
    hass.data[f"{DOMAIN}_notifications_registered"] = True

    @callback
    def _on_access_event(event: Event) -> None:
        event_type: str = event.data.get("event_type", "")
        if event_type not in _NOTIFY_ON:
            return

        from .storage import DoormanStore  # noqa: PLC0415 — avoid circular at module level

        store: DoormanStore | None = hass.data.get(f"{DOMAIN}_store")
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

        # Prefer the config entry title (e.g. "North Gate") over a generic
        # "the intercom" — many installs are keypad-only Access Units, not
        # video intercoms, and multi-device setups need to know which door.
        entry_id: str | None = event.data.get("entry_id")
        device_name: str | None = None
        ios_sound: str = ""
        android_channel: str = ""
        if entry_id:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None:
                device_name = entry.title
                ios_sound = entry.options.get(CONF_NOTIFICATION_SOUND_IOS, "") or ""
                android_channel = (
                    entry.options.get(CONF_NOTIFICATION_CHANNEL_ANDROID, "") or ""
                )
        message = (
            f"{user_name} opened {device_name}"
            if device_name
            else f"{user_name} opened the door"
        )
        # iOS Companion reads sound from data.push.sound; Android reads
        # channel from data.channel. Only include when configured so we
        # don't override user-chosen defaults with empty strings.
        data: dict = {"tag": f"doorman_{two_n_uuid}"}
        if ios_sound:
            data["push"] = {"sound": ios_sound}
        if android_channel:
            data["channel"] = android_channel
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
                        "title": "Doorman",
                        "message": message,
                        "data": data,
                    },
                )
            )
            _LOGGER.debug("Notification dispatched to %s: %s", service, message)

    hass.data[f"{DOMAIN}_notifications_unsub"] = hass.bus.async_listen(f"{DOMAIN}_access", _on_access_event)
