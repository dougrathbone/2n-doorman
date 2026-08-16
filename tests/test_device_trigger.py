"""Tests for Doorman device triggers."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN
from custom_components.doorman.device_trigger import (
    TRIGGER_TYPES,
    async_attach_trigger,
    async_get_triggers,
)


@pytest.mark.asyncio
async def test_get_triggers_lists_all_types(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """Every trigger type is offered for the Doorman device."""
    device = dr.async_get(hass).async_get_device(
        identifiers={(DOMAIN, setup_doorman.entry_id)}
    )
    assert device is not None

    triggers = await async_get_triggers(hass, device.id)
    assert {t["type"] for t in triggers} == set(TRIGGER_TYPES)
    assert all(t["domain"] == DOMAIN for t in triggers)
    assert all(t["entry_id"] == setup_doorman.entry_id for t in triggers)


@pytest.mark.asyncio
async def test_get_triggers_unknown_device(hass: HomeAssistant) -> None:
    """Unknown device ids yield no triggers."""
    assert await async_get_triggers(hass, "nonexistent-device-id") == []


@pytest.mark.asyncio
async def test_attach_trigger_fires_on_matching_event(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
) -> None:
    """An attached trigger calls its action only for the matching entry+type."""
    calls = []

    async def action(run_variables, context=None):
        calls.append(run_variables)

    from homeassistant.helpers.trigger import TriggerInfo

    trigger_info = TriggerInfo(
        domain="test",
        name="test",
        home_assistant_start=False,
        variables=None,
        trigger_data={"id": "0", "idx": "0", "alias": None},
    )

    for trig_type in ("doorbell_pressed", "access_granted"):
        attach = await async_attach_trigger(
            hass,
            {
                "platform": "device",
                "domain": DOMAIN,
                "device_id": "any",
                "type": trig_type,
                "entry_id": setup_doorman.entry_id,
            },
            action,
            trigger_info,
        )
        assert attach is not None

    # Matching: doorbell from this entry
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"entry_id": setup_doorman.entry_id, "event_type": "DoorbellPressed", "params": {}},
    )
    # Non-matching: same type from another entry, and another type from this entry
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"entry_id": "other-entry", "event_type": "DoorbellPressed", "params": {}},
    )
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"entry_id": setup_doorman.entry_id, "event_type": "TamperSwitchActivated", "params": {}},
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
