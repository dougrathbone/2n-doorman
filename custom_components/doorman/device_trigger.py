"""Device triggers for Doorman — UI-pickable automation triggers.

Exposes the ``doorman_access`` bus events as device triggers so users can
build automations from the automation editor (When → Device → Doorman …)
instead of hand-writing manual event triggers on ``doorman_access``.
"""
from __future__ import annotations

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN

# Trigger type slug → 2N/synthetic event_type on the doorman_access bus.
TRIGGER_TYPES = {
    "doorbell_pressed": "DoorbellPressed",
    "call_ringing": "CallStateChanged",
    "access_granted": "UserAuthenticated",
    "access_denied": "UserRejected",
    "unauthorized_door_open": "UnauthorizedDoorOpen",
    "door_open_too_long": "DoorOpenTooLong",
    "tamper": "TamperSwitchActivated",
}

CONF_ENTRY_ID = "entry_id"

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Required(CONF_ENTRY_ID): str,
    }
)


async def async_get_triggers(
    hass: HomeAssistant, device_id: str
) -> list[dict[str, str]]:
    """Return the triggers available for a Doorman device."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return []
    entry_ids = {cid for domain, cid in device.identifiers if domain == DOMAIN}
    return [
        {
            CONF_PLATFORM: "device",
            CONF_DEVICE_ID: device_id,
            CONF_DOMAIN: DOMAIN,
            CONF_TYPE: trigger_type,
            CONF_ENTRY_ID: entry_id,
            "metadata": {},
        }
        for entry_id in entry_ids
        for trigger_type in TRIGGER_TYPES
    ]


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
):
    """Attach an event trigger filtered to this device and event type."""
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            # A list, not a bare string: the event trigger iterates the value,
            # and only schema validation (which real automation configs pass
            # through) listifies a bare string.
            event_trigger.CONF_EVENT_TYPE: [f"{DOMAIN}_access"],
            event_trigger.CONF_EVENT_DATA: {
                CONF_ENTRY_ID: config[CONF_ENTRY_ID],
                "event_type": TRIGGER_TYPES[config[CONF_TYPE]],
            },
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info
    )
