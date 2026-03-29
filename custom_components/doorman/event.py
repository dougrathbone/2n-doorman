"""Event platform for Doorman — fires when someone authenticates at the intercom."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DoormanCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DoormanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DoormanAccessEventEntity(coordinator, entry)])


class DoormanAccessEventEntity(EventEntity):
    """Fires whenever an access event is detected on the 2N device.

    The coordinator fires ``doorman_access`` events on the HA event bus;
    this entity translates those into a proper HA Event entity so they
    can be used in automations with ``trigger: platform: event``.
    """

    _attr_event_types = [
        "authenticated",
        "rejected",
        "code_entered",
        "card_entered",
        "finger_entered",
        "mobile_key",
    ]
    _attr_icon = "mdi:shield-account"

    # Map 2N event type strings → HA event type slugs
    _EVENT_MAP = {
        "UserAuthenticated": "authenticated",
        "UserRejected": "rejected",
        "CodeEntered": "code_entered",
        "CardEntered": "card_entered",
        "FingerEntered": "finger_entered",
        "MobKeyEntered": "mobile_key",
    }

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        self._attr_unique_id = f"{entry.entry_id}_access_event"
        self._attr_name = "Doorman Access"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_access", self._handle_bus_event
            )
        )

    @callback
    def _handle_bus_event(self, event: Event) -> None:
        raw_type: str = event.data.get("event_type", "")
        ha_type = self._EVENT_MAP.get(raw_type, raw_type.lower())
        params: dict = event.data.get("params", {})

        self._trigger_event(
            ha_type,
            {
                "user_name": params.get("user", {}).get("name"),
                "user_uuid": params.get("user", {}).get("id"),
                "card": params.get("card"),
                "valid": params.get("valid"),
                "utc_time": event.data.get("utc_time"),
            },
        )
        self.async_write_ha_state()
