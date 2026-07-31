"""Event platform for Doorman — fires when someone authenticates at the intercom."""
from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
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
        "doorbell_pressed",
    ]
    _attr_icon = "mdi:shield-account"

    # Map 2N event type strings → HA event type slugs. "DoorbellPressed" is a
    # synthetic value fired by the coordinator (see DOORBELL_EVENT_TYPE) — not
    # a raw 2N event name — so keypad digits don't trigger doorbell automations.
    _EVENT_MAP = {
        "UserAuthenticated": "authenticated",
        "UserRejected": "rejected",
        "CodeEntered": "code_entered",
        "CardEntered": "card_entered",
        "FingerEntered": "finger_entered",
        "MobKeyEntered": "mobile_key",
        "DoorbellPressed": "doorbell_pressed",
    }

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        self._attr_unique_id = f"{entry.entry_id}_access_event"
        self._attr_name = "Doorman Access"
        # Keep entity IDs (event.doorman_access) stable: with device_info set,
        # newer HA versions otherwise prefix the object id with the device name.
        self._attr_has_entity_name = False
        # Pin the entity ID explicitly so device_info can't cause device-name
        # prefixes (HA 2026.7+ prefixes new device-linked entities otherwise).
        self.entity_id = "event.doorman_access"
        self._entry_id = entry.entry_id
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="2N",
            model=coordinator.device_info.get("hwVersion"),
            sw_version=coordinator.device_info.get("swVersion"),
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(
                f"{DOMAIN}_access", self._handle_bus_event
            )
        )

    @callback
    def _handle_bus_event(self, event: Event) -> None:
        # Ignore events from other Doorman config entries — otherwise every
        # entity fires for every device in a multi-device install.
        if event.data.get("entry_id") != self._entry_id:
            return

        raw_type: str = event.data.get("event_type", "")
        ha_type = self._EVENT_MAP.get(raw_type, raw_type.lower())
        params: dict = event.data.get("params", {})

        self._trigger_event(
            ha_type,
            {
                "user_name": params.get("name"),
                "user_uuid": params.get("uuid"),
                # CardEntered carries the card id as "uid" on real 2N devices;
                # "card" is kept as a defensive fallback.
                "card": params.get("uid") or params.get("card"),
                "valid": params.get("valid"),
                "utc_time": event.data.get("utc_time"),
            },
        )
        self.async_write_ha_state()
