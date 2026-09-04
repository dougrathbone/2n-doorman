"""Binary sensors for Doorman — door state and hardware inputs.

Two flavours:

- The door sensor is event-driven: the 2N device emits ``DoorStateChanged``
  log events when a door contact is configured (Hardware → Digital Inputs →
  Door State). There is no HTTP endpoint to poll for it, so the sensor
  listens on the ``doorman_access`` bus and stays ``unknown`` until the
  first event arrives.
- One sensor per hardware input port from ``/api/io/caps`` (e.g. a REX
  button or an external contact), state polled via ``/api/io/status`` and
  updated instantly by ``InputChanged`` events through the coordinator.
"""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DoormanCoordinator
from .helpers import build_device_info, pinned_entity_id


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the door sensor and one sensor per hardware input port."""
    coordinator: DoormanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = [DoormanDoorSensor(coordinator, entry)]
    entities.extend(
        DoormanInputSensor(coordinator, entry, port["port"])
        for port in coordinator.io_ports
        if port.get("type") == "input" and port.get("port")
    )
    if coordinator.phone_status_available:
        entities.append(DoormanSipRegisteredSensor(coordinator, entry))
    async_add_entities(entities)


class DoormanDoorSensor(BinarySensorEntity):
    """Physical door open/closed, driven by DoorStateChanged events."""

    _attr_name = "Doorman Door"
    _attr_device_class = BinarySensorDeviceClass.DOOR
    # Keep entity IDs stable: with device_info set, newer HA versions
    # otherwise prefix the object id with the device name.
    _attr_has_entity_name = False

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_door"
        # Pin the entity ID explicitly (see switch.py for why).
        self.entity_id = pinned_entity_id("binary_sensor", "door", coordinator, entry)
        self._entry_id = entry.entry_id
        self._attr_device_info = build_device_info(coordinator, entry)
        self._attr_is_on = None  # unknown until the first DoorStateChanged

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            self.hass.bus.async_listen(f"{DOMAIN}_access", self._handle_bus_event)
        )

    @callback
    def _handle_bus_event(self, event: Event) -> None:
        if event.data.get("entry_id") != self._entry_id:
            return
        if event.data.get("event_type") != "DoorStateChanged":
            return
        state = event.data.get("params", {}).get("state")
        if state in ("opened", "closed"):
            self._attr_is_on = state == "opened"
            self.async_write_ha_state()


class DoormanInputSensor(CoordinatorEntity[DoormanCoordinator], BinarySensorEntity):
    """One hardware input port (REX button, external contact, …)."""
    _attr_has_entity_name = False

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry, port: str
    ) -> None:
        super().__init__(coordinator)
        self._port = port
        self._attr_unique_id = f"{entry.entry_id}_input_{port}"
        self._attr_name = f"Doorman Input {port}"
        self.entity_id = pinned_entity_id(
            "binary_sensor", f"input_{port}", coordinator, entry
        )
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        for port in (self.coordinator.data or {}).get("io", []):
            if port.get("port") == self._port:
                return bool(port.get("state"))
        return None


class DoormanSipRegisteredSensor(CoordinatorEntity[DoormanCoordinator], BinarySensorEntity):
    """Whether every registration-enabled SIP account is actually registered.

    This is the "can the intercom actually ring phones" signal: an
    unregistered account means doorbell calls silently go nowhere.
    """

    _attr_name = "Doorman SIP Registered"
    _attr_has_entity_name = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_sip_registered"
        self.entity_id = pinned_entity_id(
            "binary_sensor", "sip_registered", coordinator, entry
        )
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        accounts = (self.coordinator.data or {}).get("phone_accounts", [])
        if not accounts:
            return None
        registered_required = [a for a in accounts if a.get("registrationEnabled")]
        if not registered_required:
            # Device-to-device calling only (no registrar involved) → healthy.
            return True
        return all(a.get("registered") for a in registered_required)

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "accounts": [
                {
                    "account": a.get("account"),
                    "enabled": a.get("enabled"),
                    "registration_enabled": a.get("registrationEnabled"),
                    "registered": a.get("registered"),
                }
                for a in (self.coordinator.data or {}).get("phone_accounts", [])
            ]
        }
