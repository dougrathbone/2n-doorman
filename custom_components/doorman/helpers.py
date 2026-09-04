"""Shared helpers for Doorman entity platforms."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_HOST, CONF_USE_SSL, DEFAULT_USE_SSL, DOMAIN
from .coordinator import DoormanCoordinator


def device_slug(coordinator: DoormanCoordinator, entry: ConfigEntry) -> str:
    """Return a stable short slug for entity IDs from the device serial.

    Sanitizes ``serialNumber`` to lowercase alphanumeric. If the result is
    shorter than 4 characters (missing/short serial), falls back to the first
    8 characters of ``entry.entry_id`` with hyphens stripped.
    """
    serial = str(coordinator.device_info.get("serialNumber") or "")
    sanitized = "".join(c for c in serial.lower() if c.isalnum())
    if len(sanitized) < 4:
        # Entity IDs must be lowercase; strip hyphens from UUID-style entry_ids.
        return entry.entry_id.replace("-", "").lower()[:8]
    return sanitized


def pinned_entity_id(
    platform: str,
    object_id: str,
    coordinator: DoormanCoordinator,
    entry: ConfigEntry,
) -> str:
    """Build a device-scoped entity ID: ``{platform}.doorman_{slug}_{object_id}``."""
    return f"{platform}.doorman_{device_slug(coordinator, entry)}_{object_id}"


def build_device_info(
    coordinator: DoormanCoordinator, entry: ConfigEntry
) -> DeviceInfo:
    """Build enriched DeviceInfo for all Doorman entities on one config entry."""
    info = coordinator.device_info
    host = entry.data[CONF_HOST]
    use_ssl = entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL)
    scheme = "https" if use_ssl else "http"
    serial = info.get("serialNumber") or None
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name=entry.title,
        manufacturer="2N",
        model=info.get("model") or info.get("hwVersion"),
        hw_version=info.get("hwVersion"),
        sw_version=info.get("swVersion"),
        serial_number=serial,
        configuration_url=f"{scheme}://{host}/",
    )
