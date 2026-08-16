"""Button platform for Doorman — device restart."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import DoormanApiError
from .const import DOMAIN
from .coordinator import DoormanCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the restart button when the System API answered at startup."""
    coordinator: DoormanCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.system_status_available:
        async_add_entities([DoormanRestartButton(coordinator, entry)])


class DoormanRestartButton(CoordinatorEntity[DoormanCoordinator], ButtonEntity):
    """Reboot the 2N device (drops calls and the log subscription briefly)."""

    _attr_name = "Doorman Restart"
    _attr_has_entity_name = False
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_restart"
        self.entity_id = "button.doorman_restart"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="2N",
            model=coordinator.device_info.get("hwVersion"),
            sw_version=coordinator.device_info.get("swVersion"),
        )

    async def async_press(self) -> None:
        try:
            await self.coordinator.client.restart_device()
        except DoormanApiError as err:
            raise HomeAssistantError(f"Restart failed on the 2N device: {err}") from err
        _LOGGER.info(
            "Doorman: restart requested for %s",
            self.coordinator.device_info.get("deviceName") or "the 2N device",
        )
