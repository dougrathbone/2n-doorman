"""Switch platform for Doorman — maps 2N relays to HA switch entities."""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
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
    async_add_entities(
        DoormanRelay(coordinator, entry, sw)
        for sw in coordinator.data.get("switches", [])
    )


class DoormanRelay(CoordinatorEntity[DoormanCoordinator], SwitchEntity):
    """A 2N relay/switch mapped to a HA switch entity."""

    _attr_icon = "mdi:door"

    def __init__(
        self,
        coordinator: DoormanCoordinator,
        entry: ConfigEntry,
        switch_data: dict,
    ) -> None:
        super().__init__(coordinator)
        self._switch_id: int = switch_data["id"]
        self._attr_unique_id = f"{entry.entry_id}_relay_{self._switch_id}"
        # Always use a predictable name so entity_id is stable across renames on the device
        self._attr_name = f"Doorman Relay {self._switch_id}"
        self._attr_extra_state_attributes = {"device_name": switch_data.get("name", "")}

    @property
    def is_on(self) -> bool:
        for sw in (self.coordinator.data or {}).get("switches", []):
            if sw["id"] == self._switch_id:
                return bool(sw.get("active", False))
        return False

    async def async_turn_on(self, **kwargs) -> None:  # noqa: ANN003
        await self.coordinator.client.set_switch(self._switch_id, "on")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:  # noqa: ANN003
        await self.coordinator.client.set_switch(self._switch_id, "off")
        await self.coordinator.async_request_refresh()
