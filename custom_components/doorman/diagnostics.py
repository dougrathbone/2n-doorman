"""Diagnostics support for Doorman."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOST, CONF_USERNAME, DOMAIN
from .coordinator import DoormanCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a Doorman config entry."""
    coordinator: DoormanCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)

    diag: dict[str, Any] = {
        "config_entry": {
            "host": entry.data[CONF_HOST],
            "username": entry.data[CONF_USERNAME],
            "password": "**REDACTED**",
        },
        "options": dict(entry.options),
    }

    if coordinator is None:
        diag["coordinator"] = "not_loaded"
        return diag

    diag["coordinator"] = {
        "device_info": coordinator.device_info,
        "has_write_permission": coordinator.has_write_permission,
        "access_points": coordinator.access_points,
        "log_task_running": (
            coordinator._log_task is not None and not coordinator._log_task.done()  # noqa: SLF001
        ),
        "log_buffer_size": len(coordinator._log_buffer),  # noqa: SLF001
        "user_count": len((coordinator.data or {}).get("users", [])),
        "switch_count": len((coordinator.data or {}).get("switches", [])),
    }
    return diag
