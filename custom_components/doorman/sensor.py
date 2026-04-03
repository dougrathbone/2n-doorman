"""Sensor platform for Doorman — exposes user count and device info."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import DoormanCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: DoormanCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([DoormanUserCountSensor(coordinator, entry)])


class DoormanUserCountSensor(CoordinatorEntity[DoormanCoordinator], SensorEntity):
    """Number of users currently in the 2N directory."""

    _attr_icon = "mdi:account-multiple"
    _attr_name = "Doorman User Count"

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_user_count"

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("users", []))

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "users": [
                {"uuid": u.get("uuid"), "name": u.get("name")}
                for u in (self.coordinator.data or {}).get("users", [])
            ]
        }
