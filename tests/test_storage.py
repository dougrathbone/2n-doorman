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

    await store.update_last_access("uuid-jane", 1743242400)
    assert store.last_access["uuid-jane"] == 1743242400

    # Reload to verify persistence
    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.last_access["uuid-jane"] == 1743242400


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


@pytest.mark.asyncio
async def test_store_instances_do_not_share_default_state(hass: HomeAssistant) -> None:
    """Two fresh stores must not alias the same nested default dicts.

    Regression: a shallow copy of a shared module-level default would let a
    mutation on one store leak into every other store (and across reloads).
    """
    store_a = DoormanStore(hass)
    store_b = DoormanStore(hass)

    store_a._data.setdefault("user_links", {})["x"] = "y"

    assert store_b.user_links == {}
    assert store_a.user_links == {"x": "y"}


@pytest.mark.asyncio
async def test_update_last_access_batch_persists_all(hass: HomeAssistant) -> None:
    """update_last_access_batch records every entry and survives a reload."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.update_last_access_batch(
        [("uuid-jane", "2026-03-29T10:00:00Z"), ("uuid-john", "2026-03-29T11:00:00Z")]
    )

    assert store.last_access["uuid-jane"] == "2026-03-29T10:00:00Z"
    assert store.last_access["uuid-john"] == "2026-03-29T11:00:00Z"

    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.last_access["uuid-john"] == "2026-03-29T11:00:00Z"


@pytest.mark.asyncio
async def test_update_last_access_batch_empty_is_noop(hass: HomeAssistant) -> None:
    """An empty batch does not write anything."""
    store = DoormanStore(hass)
    await store.async_load()
    await store.update_last_access_batch([])
    assert store.last_access == {}


@pytest.mark.asyncio
async def test_clear_notification_targets(hass: HomeAssistant) -> None:
    """clear_notification_targets removes the entry and persists the change."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.set_notification_targets("uuid-jane", ["notify.mobile_app"])
    await store.clear_notification_targets("uuid-jane")
    assert store.get_notification_targets("uuid-jane") == []

    # Clearing an unknown UUID is a no-op and does not raise
    await store.clear_notification_targets("uuid-unknown")

    # Reload to verify persistence
    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.get_notification_targets("uuid-jane") == []


# ─── Per-entry notification settings ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_notification_settings_defaults(hass: HomeAssistant) -> None:
    """An entry with nothing stored gets the full default set, not a KeyError."""
    store = DoormanStore(hass)
    await store.async_load()

    assert store.get_notification_settings("entry-1") == {
        "access_sound_ios": "",
        "access_channel_android": "",
        "doorbell_sound_ios": "",
        "doorbell_channel_android": "",
        "doorbell_key_code": "%1",
        "doorbell_targets": [],
    }


@pytest.mark.asyncio
async def test_notification_settings_persist_and_merge(hass: HomeAssistant) -> None:
    """Partial updates merge into the stored settings and survive a reload."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.set_notification_settings("entry-1", {"doorbell_key_code": "%2"})
    settings = await store.set_notification_settings(
        "entry-1", {"doorbell_targets": ["notify.phone"]}
    )

    assert settings["doorbell_key_code"] == "%2"
    assert settings["doorbell_targets"] == ["notify.phone"]

    store2 = DoormanStore(hass)
    await store2.async_load()
    assert store2.get_notification_settings("entry-1")["doorbell_key_code"] == "%2"


@pytest.mark.asyncio
async def test_notification_settings_are_isolated_per_entry(hass: HomeAssistant) -> None:
    """The store is shared across config entries, so settings must be keyed by entry_id."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.set_notification_settings("entry-a", {"doorbell_targets": ["notify.a"]})
    await store.set_notification_settings("entry-b", {"doorbell_targets": ["notify.b"]})

    assert store.get_notification_settings("entry-a")["doorbell_targets"] == ["notify.a"]
    assert store.get_notification_settings("entry-b")["doorbell_targets"] == ["notify.b"]


@pytest.mark.asyncio
async def test_get_notification_settings_returns_a_copy(hass: HomeAssistant) -> None:
    """Mutating the returned dict must not corrupt the stored state."""
    store = DoormanStore(hass)
    await store.async_load()
    await store.set_notification_settings("entry-1", {"doorbell_targets": ["notify.a"]})

    settings = store.get_notification_settings("entry-1")
    settings["doorbell_targets"].append("notify.b")
    settings["doorbell_key_code"] = "%9"

    assert store.get_notification_settings("entry-1")["doorbell_targets"] == ["notify.a"]
    assert store.get_notification_settings("entry-1")["doorbell_key_code"] == "%1"


@pytest.mark.asyncio
async def test_async_load_adds_new_keys_to_an_older_store_file(
    hass: HomeAssistant, hass_storage
) -> None:
    """A .storage file written before this key existed still loads cleanly.

    Additive schema changes layer over the empty defaults, so no
    STORAGE_VERSION bump (and no migration) is needed.
    """
    hass_storage["doorman.storage"] = {
        "version": 1,
        "minor_version": 1,
        "key": "doorman.storage",
        "data": {"user_links": {"uuid-jane": "ha-1"}, "notification_targets": {}},
    }

    store = DoormanStore(hass)
    await store.async_load()

    assert store.get_ha_user_id("uuid-jane") == "ha-1"
    assert store.notification_settings == {}
    assert store.last_access == {}
    assert store.get_notification_settings("entry-1")["doorbell_key_code"] == "%1"
