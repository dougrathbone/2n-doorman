"""Tests for the Doorman event platform (access event entity)."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import DOMAIN
from tests.conftest import setup_two_entries


@pytest.mark.asyncio
async def test_event_entity_created_on_setup(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """The access event entity is created when the integration loads."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    state = hass.states.get("event.doorman_access")
    assert state is not None


@pytest.mark.asyncio
async def test_bus_event_triggers_entity(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A doorman_access bus event with UserAuthenticated triggers the entity."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": doorman_config_entry.entry_id,
            "event_type": "UserAuthenticated",
            "utc_time": "2026-03-29T10:00:00Z",
            "params": {
                "ap": 0,
                "session": 1,
                "name": "Jane Doe",
                "uuid": "uuid-jane",
                "valid": True,
            },
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("event.doorman_access")
    assert state is not None
    assert state.attributes.get("event_type") == "authenticated"
    assert state.attributes.get("user_name") == "Jane Doe"
    assert state.attributes.get("user_uuid") == "uuid-jane"
    assert state.attributes.get("valid") is True
    assert state.attributes.get("utc_time") == "2026-03-29T10:00:00Z"


@pytest.mark.asyncio
async def test_unknown_event_type_does_not_update_state(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """An unknown event type not in _attr_event_types does not update entity state.

    The entity lowercases unmapped types, but HA's EventEntity._trigger_event
    rejects types not declared in _attr_event_types with a ValueError.
    Since the bus handler is a callback, the error is logged but the entity
    state remains unchanged.
    """
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    state_before = hass.states.get("event.doorman_access")

    # Fire an event with an unmapped type — the ValueError is caught internally
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": doorman_config_entry.entry_id,
            "event_type": "SomethingNewAndUnmapped",
            "params": {},
        },
    )
    await hass.async_block_till_done()

    state_after = hass.states.get("event.doorman_access")
    # The event_type attribute should remain unchanged (None from initial state)
    assert state_after.attributes.get("event_type") == state_before.attributes.get("event_type")


@pytest.mark.asyncio
async def test_known_event_type_lowercased_via_map(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """All known 2N event types map to valid HA event types via _EVENT_MAP."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    # CodeEntered -> code_entered (a mapped type)
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": doorman_config_entry.entry_id,
            "event_type": "CodeEntered",
            "params": {"code": "1234", "valid": True, "uuid": "uuid-jane"},
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("event.doorman_access")
    assert state is not None
    assert state.attributes.get("event_type") == "code_entered"


@pytest.mark.asyncio
async def test_rejected_event_type(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A UserRejected event maps to the 'rejected' type."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": doorman_config_entry.entry_id,
            "event_type": "UserRejected",
            "params": {
                "name": "Bad Actor",
                "uuid": "uuid-bad",
                "valid": False,
            },
        },
    )
    await hass.async_block_till_done()

    state = hass.states.get("event.doorman_access")
    assert state is not None
    assert state.attributes.get("event_type") == "rejected"
    assert state.attributes.get("user_name") == "Bad Actor"
    assert state.attributes.get("valid") is False


@pytest.mark.asyncio
async def test_bus_event_only_triggers_matching_entry(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A bus event with entry_id=A must only update entity A, not B.

    Regression: without the entry_id filter, every DoormanAccessEventEntity
    listens on the same global doorman_access topic and each fires for
    every device — so North Gate's PIN entry appears on Front Door's
    access log entity too.
    """
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    # Fire an event tagged with entry1's ID only
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry1.entry_id,
            "event_type": "UserAuthenticated",
            "utc_time": "2026-03-29T12:00:00Z",
            "params": {"name": "Jane Doe", "uuid": "uuid-jane"},
        },
    )
    await hass.async_block_till_done()

    # The two entities have unique_ids "{entry_id}_access_event"; HA slugs
    # them to event.doorman_access and event.doorman_access_2.
    state1 = hass.states.get("event.doorman_access")
    state2 = hass.states.get("event.doorman_access_2")
    assert state1 is not None
    assert state2 is not None

    # Exactly one of the two entities should have been updated with the
    # user_name attribute; the other should still show None.
    names = (state1.attributes.get("user_name"), state2.attributes.get("user_name"))
    assert names.count("Jane Doe") == 1, (
        f"Expected one entity to fire, got names={names!r}"
    )
    assert names.count(None) == 1
