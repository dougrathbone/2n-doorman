"""Tests for the DoormanStore persistent storage helper."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.doorman.storage import AccessLogStore, DoormanStore


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
async def test_clear_entry_removes_settings_and_prunes_uuids(
    hass: HomeAssistant,
) -> None:
    """clear_entry drops entry settings and only the supplied UUID-keyed rows."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.set_notification_settings("entry-a", {"doorbell_key_code": "%2"})
    await store.set_notification_settings("entry-b", {"doorbell_key_code": "%3"})
    await store.link_user("uuid-jane", "ha-1")
    await store.link_user("uuid-other", "ha-2")
    await store.set_notification_targets("uuid-jane", ["notify.phone"])
    await store.set_notification_targets("uuid-other", ["notify.tablet"])
    await store.update_last_access("uuid-jane", 1743242400)
    await store.update_last_access("uuid-other", 1743242500)

    await store.clear_entry("entry-a", ["uuid-jane"])

    assert "entry-a" not in store.notification_settings
    assert store.get_notification_settings("entry-b")["doorbell_key_code"] == "%3"
    assert store.get_ha_user_id("uuid-jane") is None
    assert store.get_ha_user_id("uuid-other") == "ha-2"
    assert store.get_notification_targets("uuid-jane") == []
    assert store.get_notification_targets("uuid-other") == ["notify.tablet"]
    assert "uuid-jane" not in store.last_access
    assert store.last_access["uuid-other"] == 1743242500

    store2 = DoormanStore(hass)
    await store2.async_load()
    assert "entry-a" not in store2.notification_settings
    assert store2.get_ha_user_id("uuid-jane") is None
    assert store2.get_ha_user_id("uuid-other") == "ha-2"


@pytest.mark.asyncio
async def test_clear_entry_settings_only_without_uuids(hass: HomeAssistant) -> None:
    """Without UUID list, clear_entry only removes notification_settings."""
    store = DoormanStore(hass)
    await store.async_load()

    await store.set_notification_settings("entry-a", {"doorbell_key_code": "%2"})
    await store.link_user("uuid-jane", "ha-1")
    await store.update_last_access("uuid-jane", 1743242400)

    await store.clear_entry("entry-a")

    assert "entry-a" not in store.notification_settings
    assert store.get_ha_user_id("uuid-jane") == "ha-1"
    assert store.last_access["uuid-jane"] == 1743242400


@pytest.mark.asyncio
async def test_clear_entry_noop_when_nothing_to_clear(hass: HomeAssistant) -> None:
    """clear_entry with an unknown entry and no UUIDs does not raise."""
    store = DoormanStore(hass)
    await store.async_load()
    await store.clear_entry("missing-entry", ["uuid-unknown"])
    assert store.notification_settings == {}


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


# ─── AccessLogStore ──────────────────────────────────────────────────────────


def _event(event_id: int, utc_time: int, name: str = "UserAuthenticated") -> dict:
    """Return a minimal 2N log event."""
    return {"id": event_id, "event": name, "utcTime": utc_time, "params": {}}


@pytest.mark.asyncio
async def test_access_log_store_starts_empty(hass: HomeAssistant) -> None:
    """A store with nothing on disk loads as an empty history."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()

    assert store.events == []


@pytest.mark.asyncio
async def test_access_log_store_persists_across_reload(hass: HomeAssistant) -> None:
    """Events written by one store instance are visible to the next (restart)."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()
    store.add_events([_event(1, 1743242400), _event(2, 1743242460)])
    await store.async_flush()

    reloaded = AccessLogStore(hass, "entry-1")
    await reloaded.async_load()

    assert [e["id"] for e in reloaded.events] == [1, 2]


@pytest.mark.asyncio
async def test_access_log_store_dedupes_by_id(hass: HomeAssistant) -> None:
    """Replaying the same events (e.g. a repeated backfill) keeps one copy."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()

    events = [_event(1, 1743242400), _event(2, 1743242460)]
    assert store.add_events(events) == 2
    assert store.add_events(events) == 0
    assert store.add_events([_event(2, 1743242460), _event(3, 1743242520)]) == 1

    assert [e["id"] for e in store.events] == [1, 2, 3]


@pytest.mark.asyncio
async def test_access_log_store_dedupe_survives_device_reboot(
    hass: HomeAssistant,
) -> None:
    """A rebooted device restarts its id sequence — same id, new time is a new event."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()

    store.add_events([_event(1, 1743242400), _event(2, 1743242460)])
    # Device rebooted: ids restart at 1 but the wall clock moved on.
    added = store.add_events([_event(1, 1743500000), _event(2, 1743500060)])

    assert added == 2
    assert [(e["id"], e["utcTime"]) for e in store.events] == [
        (1, 1743242400),
        (2, 1743242460),
        (1, 1743500000),
        (2, 1743500060),
    ]


@pytest.mark.asyncio
async def test_access_log_store_trims_oldest_first(hass: HomeAssistant) -> None:
    """The stored history is capped, dropping the oldest events."""
    store = AccessLogStore(hass, "entry-1", max_events=5)
    await store.async_load()

    store.add_events([_event(i, 1743242400 + i) for i in range(10)])

    assert len(store.events) == 5
    assert [e["id"] for e in store.events] == [5, 6, 7, 8, 9]

    store.add_events([_event(99, 1743242500)])
    assert [e["id"] for e in store.events] == [6, 7, 8, 9, 99]


@pytest.mark.asyncio
async def test_access_log_store_keeps_entries_separate(hass: HomeAssistant) -> None:
    """Two config entries have independent histories."""
    store_a = AccessLogStore(hass, "entry-a")
    store_b = AccessLogStore(hass, "entry-b")
    await store_a.async_load()
    await store_b.async_load()

    store_a.add_events([_event(1, 1743242400)])
    store_b.add_events([_event(1, 1743242400), _event(2, 1743242460)])
    await store_a.async_flush()
    await store_b.async_flush()

    reloaded_a = AccessLogStore(hass, "entry-a")
    reloaded_b = AccessLogStore(hass, "entry-b")
    await reloaded_a.async_load()
    await reloaded_b.async_load()

    assert len(reloaded_a.events) == 1
    assert len(reloaded_b.events) == 2


@pytest.mark.asyncio
async def test_access_log_store_coalesces_writes(hass: HomeAssistant) -> None:
    """A burst of events produces one delayed write, not one per event."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()

    with (
        patch.object(store._store, "async_delay_save") as delay_save,
        patch.object(store._store, "async_save", new=AsyncMock()) as save_now,
    ):
        store.add_events([_event(i, 1743242400 + i) for i in range(20)])

        assert delay_save.call_count == 1
        save_now.assert_not_called()


@pytest.mark.asyncio
async def test_access_log_store_no_write_when_nothing_new(hass: HomeAssistant) -> None:
    """Adding only duplicates schedules no write at all."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()
    store.add_events([_event(1, 1743242400)])

    with patch.object(store._store, "async_delay_save") as delay_save:
        store.add_events([_event(1, 1743242400)])
        assert delay_save.call_count == 0


@pytest.mark.asyncio
async def test_access_log_store_flush_is_noop_when_clean(hass: HomeAssistant) -> None:
    """Flushing with no pending changes does not hit the disk."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()

    with patch.object(store._store, "async_save", new=AsyncMock()) as save_now:
        await store.async_flush()
        save_now.assert_not_called()

        store.add_events([_event(1, 1743242400)])
        await store.async_flush()
        assert save_now.await_count == 1

        # A second flush without new events writes nothing more
        await store.async_flush()
        assert save_now.await_count == 1


@pytest.mark.asyncio
async def test_access_log_store_ignores_malformed_events(hass: HomeAssistant) -> None:
    """Non-dict junk from the device is skipped rather than stored."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()

    added = store.add_events(["not-an-event", None, _event(1, 1743242400)])

    assert added == 1
    assert [e["id"] for e in store.events] == [1]


@pytest.mark.asyncio
async def test_access_log_store_remove_deletes_history(hass: HomeAssistant) -> None:
    """async_remove clears the persisted history for a removed config entry."""
    store = AccessLogStore(hass, "entry-1")
    await store.async_load()
    store.add_events([_event(1, 1743242400)])
    await store.async_flush()

    await AccessLogStore(hass, "entry-1").async_remove()

    reloaded = AccessLogStore(hass, "entry-1")
    await reloaded.async_load()
    assert reloaded.events == []
