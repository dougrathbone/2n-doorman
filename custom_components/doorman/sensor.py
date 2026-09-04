"""Sensor platform for Doorman — exposes user count and device info."""
from __future__ import annotations

from datetime import UTC, datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
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
    coordinator: DoormanCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [DoormanUserCountSensor(coordinator, entry)]
    if coordinator.system_status_available:
        entities.append(DoormanUptimeSensor(coordinator, entry))
    async_add_entities(entities)


class DoormanUserCountSensor(CoordinatorEntity[DoormanCoordinator], SensorEntity):
    """Number of users currently in the 2N directory."""

    _attr_icon = "mdi:account-multiple"
    _attr_name = "Doorman User Count"
    # Keep entity IDs stable: with device_info set, newer HA versions
    # otherwise prefix the object id with the device name.
    _attr_has_entity_name = False

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_user_count"
        # Pin the entity ID: with device_info set, HA 2026.7+ would otherwise
        # generate sensor.<device_name>_…. Setting entity_id is the supported
        # way for an integration to keep stable object IDs.
        self.entity_id = pinned_entity_id(
            "sensor", "user_count", coordinator, entry
        )
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("users", []))


class DoormanUptimeSensor(CoordinatorEntity[DoormanCoordinator], SensorEntity):
    """Device boot time, derived from /api/system/status (systemTime - upTime).

    A timestamp sensor (rather than a seconds-since-boot duration) so the
    state only changes when the device actually reboots, not on every poll.
    """

    _attr_name = "Doorman Uptime"
    _attr_has_entity_name = False
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:clock-check-outline"

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_uptime"
        self.entity_id = pinned_entity_id("sensor", "uptime", coordinator, entry)
        self._attr_device_info = build_device_info(coordinator, entry)

    @property
    def native_value(self) -> datetime | None:
        status = (self.coordinator.data or {}).get("system_status", {})
        system_time = status.get("systemTime")
        up_time = status.get("upTime")
        if not system_time or up_time is None:
            return None
        return datetime.fromtimestamp(system_time - up_time, UTC)
