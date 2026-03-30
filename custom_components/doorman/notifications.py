"""Push notification dispatch for Doorman access events.

Listens on the HA bus for ``doorman_access`` events fired by the
coordinator.  When a user successfully authenticates and that user has
notification targets configured, a ``notify.*`` service call is made for
each target.
"""
from __future__ import annotations

import logging

from homeassistant.core import Event, HomeAssistant, callback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Only fire push notifications for successful entry — not rejected attempts
_NOTIFY_ON = frozenset({"UserAuthenticated"})


@callback
def async_setup_notifications(hass: HomeAssistant) -> None:
    """Register the access-event listener that dispatches push notifications."""

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
        user_info: dict = params.get("user", {})
        two_n_uuid: str | None = user_info.get("id")
        user_name: str = user_info.get("name") or "Someone"

        if not two_n_uuid:
            _LOGGER.debug("Access event has no user UUID — skipping notifications")
            return

        # Resolve follower UUID → leader UUID so notification targets
        # configured on the leader work for events from the follower
        lookup_uuid = two_n_uuid
        leader_uuid = store.get_leader_uuid_for_follower(two_n_uuid)
        if leader_uuid:
            lookup_uuid = leader_uuid

        targets = store.get_notification_targets(lookup_uuid)
        if not targets:
            return

        message = f"{user_name} opened the intercom"
        for target in targets:
            # Stored as "notify.service_name"; strip the domain prefix for the call
            service = target.removeprefix("notify.")
            hass.async_create_task(
                hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": "Doorman",
                        "message": message,
                        "data": {"tag": f"doorman_{two_n_uuid}"},
                    },
                )
            )
            _LOGGER.debug("Notification dispatched to %s: %s", service, message)

    hass.bus.async_listen(f"{DOMAIN}_access", _on_access_event)
