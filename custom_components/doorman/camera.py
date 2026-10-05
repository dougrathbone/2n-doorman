"""Camera platform for Doorman — still-image snapshots from the intercom camera.

The 2N HTTP API exposes JPEG snapshots only (no stream), so this is a classic
still-image camera: HA calls ``async_camera_image`` when the picture needs
refreshing (picture entity, glance card, notification attachment, ...).
"""
from __future__ import annotations

import asyncio
import logging
import time

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api_client import DoormanApiError
from .const import DOMAIN
from .coordinator import DoormanCoordinator
from .helpers import build_device_info, pinned_entity_id

_LOGGER = logging.getLogger(__name__)

# Preferred snapshot size; the device only accepts resolutions advertised by
# /api/camera/caps, so fall back to the largest advertised one if absent.
_PREFERRED_RESOLUTION = (640, 480)
# Coalesce multi-phone Companion fetches (and brief failure cooldowns) so one
# doorbell ring does not stampede /api/camera/snapshot.
_SNAPSHOT_CACHE_SECONDS = 2.0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the camera entity when the device reports a camera."""
    coordinator: DoormanCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.camera_caps:
        return
    async_add_entities([DoormanCamera(coordinator, entry)])


class DoormanCamera(CoordinatorEntity[DoormanCoordinator], Camera):
    """Still-image camera backed by /api/camera/snapshot."""

    _attr_name = "Doorman Camera"
    # Keep entity IDs stable: with device_info set, newer HA versions
    # otherwise prefix the object id with the device name.
    _attr_has_entity_name = False

    def __init__(
        self, coordinator: DoormanCoordinator, entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        Camera.__init__(self)
        self._attr_unique_id = f"{entry.entry_id}_camera"
        # Pin the entity ID explicitly so device_info can't cause device-name
        # prefixes (HA 2026.7+ prefixes new device-linked entities otherwise).
        self.entity_id = pinned_entity_id("camera", "camera", coordinator, entry)
        self._attr_device_info = build_device_info(coordinator, entry)
        self._width, self._height = self._pick_resolution()
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_cache: bytes | None = None
        self._snapshot_at = 0.0

    def _pick_resolution(self) -> tuple[int, int]:
        """Choose the preferred snapshot size, or the largest advertised one."""
        resolutions = self.coordinator.camera_caps.get("jpegResolution", [])
        available = {
            (int(r["width"]), int(r["height"]))
            for r in resolutions
            if r.get("width") and r.get("height")
        }
        if _PREFERRED_RESOLUTION in available or not available:
            return _PREFERRED_RESOLUTION
        return max(available, key=lambda r: r[0] * r[1])

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a JPEG snapshot; None on device error (HA shows unavailable)."""
        async with self._snapshot_lock:
            now = time.monotonic()
            if (now - self._snapshot_at) < _SNAPSHOT_CACHE_SECONDS:
                return self._snapshot_cache
            try:
                image = await self.coordinator.client.get_camera_snapshot(
                    self._width, self._height
                )
            except DoormanApiError as err:
                _LOGGER.warning("Doorman: camera snapshot failed (%s)", err)
                # Negative cache: queued waiters must not each retry the device.
                self._snapshot_cache = None
                self._snapshot_at = time.monotonic()
                return None
            self._snapshot_cache = image
            self._snapshot_at = time.monotonic()
            return image
