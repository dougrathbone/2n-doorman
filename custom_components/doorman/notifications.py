"""Push notification dispatch for Doorman access events.

Listens on the HA bus for ``doorman_access`` events fired by the
coordinator.  Two flows exist:

* ``UserAuthenticated`` — dispatch per-user notifications to the notify
  targets that the operator configured for that specific 2N user.
* ``DoorbellPressed`` — dispatch per-device notifications to the notify
  targets configured under the entry's ``doorbell_targets`` option.
"""
from __future__ import annotations

import logging
from typing import Iterable

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback

from .const import CONF_DOORBELL_TARGETS, DOMAIN

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
        elif event_type == "DoorbellPressed":
            _handle_doorbell_pressed(hass, event, entry)

    hass.data[f"{DOMAIN}_notifications_unsub"] = hass.bus.async_listen(
        f"{DOMAIN}_access", _on_access_event
    )


def _lookup_entry(hass: HomeAssistant, entry_id: str | None) -> ConfigEntry | None:
    if not entry_id:
        return None
    return hass.config_entries.async_get_entry(entry_id)


def _handle_user_authenticated(
    hass: HomeAssistant, event: Event, entry: ConfigEntry | None
) -> None:
    # Imported here to avoid circular at module import (notifications is
    # loaded during setup, before storage is fully wired up).
    from .storage import DoormanStore  # noqa: PLC0415

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

    device_name = entry.title if entry is not None else None
    message = (
        f"{user_name} opened {device_name}"
        if device_name
        else f"{user_name} opened the door"
    )
    _dispatch(hass, targets, "Doorman", message, tag=f"doorman_{two_n_uuid}")


def _handle_doorbell_pressed(
    hass: HomeAssistant, event: Event, entry: ConfigEntry | None
) -> None:
    if entry is None:
        # Doorbell targets are stored per-entry — without an entry there's
        # nowhere to look up who to notify.
        _LOGGER.debug("Doorbell event has no config entry — skipping notifications")
        return

    targets = entry.options.get(CONF_DOORBELL_TARGETS) or []
    if not targets:
        return

    device_name = entry.title
    message = (
        f"{device_name}: someone rang the doorbell"
        if device_name
        else "Someone rang the doorbell"
    )
    _dispatch(hass, targets, "Doorbell", message, tag=f"doorman_doorbell_{entry.entry_id}")


def _dispatch(
    hass: HomeAssistant,
    targets: Iterable[str],
    title: str,
    message: str,
    *,
    tag: str,
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
                    "data": {"tag": tag},
                },
            )
        )
        _LOGGER.debug("Notification dispatched to %s: %s", service, message)
