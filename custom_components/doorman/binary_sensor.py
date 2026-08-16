"""Binary sensors for Doorman — door state and hardware inputs.

Two flavours:

- ``binary_sensor.doorman_door`` is event-driven: the 2N device emits
  ``DoorStateChanged`` log events when a door contact is configured
  (Hardware → Digital Inputs → Door State). There is no HTTP endpoint to
  poll for it, so the sensor listens on the ``doorman_access`` bus and
  stays ``unknown`` until the first event arrives.
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
from homeassistant.core import Event, HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DoormanCoordinator


def _device_info(coordinator: DoormanCoordinator, entry: ConfigEntry) -> DeviceInfo:
    """Shared device registry info for all Doorman entities."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="2N",
        model=coordinator.device_info.get("hwVersion"),
        sw_version=coordinator.device_info.get("swVersion"),
    )


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
        self.entity_id = "binary_sensor.doorman_door"
        self._entry_id = entry.entry_id
        self._attr_device_info = _device_info(coordinator, entry)
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
        self.entity_id = f"binary_sensor.doorman_input_{port}"
        self._attr_device_info = _device_info(coordinator, entry)

    @property
    def is_on(self) -> bool | None:
        for port in (self.coordinator.data or {}).get("io", []):
            if port.get("port") == self._port:
                return bool(port.get("state"))
        return None
