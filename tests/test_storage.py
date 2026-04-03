"""Tests for the DoormanStore persistent storage helper."""
from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant

from custom_components.doorman.storage import DoormanStore


@pytest.mark.asyncio
async def test_async_load_empty_defaults(hass: HomeAssistant) -> None:
    """async_load creates the store with empty defaults when no data exists."""
    store = DoormanStore(hass)
    await store.async_load()

    assert store.user_links == {}
    assert store.notification_targets == {}
    assert store.last_access == {}


@pytest.mark.asyncio
async def test_link_user_persists(hass: HomeAssistant) -> None:
    """link_user persists user links and they survive a reload."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.link_user("uuid-jane", "ha-user-1")
    assert store.get_ha_user_id("uuid-jane") == "ha-user-1"
    assert store.get_two_n_uuid("ha-user-1") == "uuid-jane"

    # Reload from disk to verify persistence
    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.get_ha_user_id("uuid-jane") == "ha-user-1"


@pytest.mark.asyncio
async def test_unlink_user_persists(hass: HomeAssistant) -> None:
    """unlink_user removes the link and persists the change."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.link_user("uuid-jane", "ha-user-1")
    await store.unlink_user("uuid-jane")

    assert store.get_ha_user_id("uuid-jane") is None
    assert store.get_two_n_uuid("ha-user-1") is None

    # Reload to verify persistence
    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.get_ha_user_id("uuid-jane") is None


@pytest.mark.asyncio
async def test_unlink_nonexistent_user_is_noop(hass: HomeAssistant) -> None:
    """unlink_user with a nonexistent UUID does not raise."""
    store = DoormanStore(hass)
    await store.async_load()

    # Should not raise
    await store.unlink_user("uuid-nonexistent")
    assert store.user_links == {}


@pytest.mark.asyncio
async def test_notification_targets(hass: HomeAssistant) -> None:
    """get/set_notification_targets round-trip correctly."""
    store = DoormanStore(hass)
    await store.async_load()

    assert store.get_notification_targets("uuid-jane") == []

    await store.set_notification_targets("uuid-jane", ["notify.mobile_app_phone"])
    assert store.get_notification_targets("uuid-jane") == ["notify.mobile_app_phone"]

    # Reload to verify persistence
    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.get_notification_targets("uuid-jane") == ["notify.mobile_app_phone"]


@pytest.mark.asyncio
async def test_update_last_access(hass: HomeAssistant) -> None:
    """update_last_access stores timestamps and persists them."""
    store = DoormanStore(hass)
    await store.async_load()

    assert store.last_access == {}

    await store.update_last_access("uuid-jane", "2026-03-29T10:00:00Z")
    assert store.last_access["uuid-jane"] == "2026-03-29T10:00:00Z"

    # Reload to verify persistence
    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.last_access["uuid-jane"] == "2026-03-29T10:00:00Z"


@pytest.mark.asyncio
async def test_multiple_user_links(hass: HomeAssistant) -> None:
    """Multiple user links can be stored and queried independently."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.link_user("uuid-jane", "ha-user-1")
    await store.link_user("uuid-john", "ha-user-2")

    assert store.get_ha_user_id("uuid-jane") == "ha-user-1"
    assert store.get_ha_user_id("uuid-john") == "ha-user-2"
    assert store.get_two_n_uuid("ha-user-1") == "uuid-jane"
    assert store.get_two_n_uuid("ha-user-2") == "uuid-john"
    assert len(store.user_links) == 2
