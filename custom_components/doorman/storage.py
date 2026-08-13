"""Persistent HA-side storage for Doorman.

``DoormanStore`` stores per-user metadata:
  user_links           — 2N UUID → HA User ID (for identity linking)
  notification_targets — 2N UUID → list of notify.* service targets
  last_access          — 2N UUID → epoch seconds of the last successful access

…and per-config-entry metadata:
  notification_settings — entry_id → per-flow notification configuration

``AccessLogStore`` stores the durable access-log history, one file per
config entry.
"""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import (
    CONF_ACCESS_CHANNEL_ANDROID,
    CONF_ACCESS_SOUND_IOS,
    CONF_DOORBELL_CHANNEL_ANDROID,
    CONF_DOORBELL_KEY_CODE,
    CONF_DOORBELL_SOUND_IOS,
    CONF_DOORBELL_TARGETS,
    DEFAULT_DOORBELL_KEY_CODE,
    LOG_SAVE_DELAY,
    LOG_STORAGE_KEY,
    LOG_STORAGE_VERSION,
    MAX_STORED_LOG_EVENTS,
    STORAGE_KEY,
    STORAGE_VERSION,
)


def _empty_data() -> dict:
    """Return a fresh, independent empty-data structure.

    A factory (not a shared module-level constant) is essential: returning a
    copy of a shared dict would alias the nested dicts, so a mutation on one
    store instance would leak into the module global and every other instance.
    """
    return {
        "user_links": {},
        "notification_targets": {},
        "last_access": {},
        "notification_settings": {},
    }


def _default_notification_settings() -> dict:
    """Return the default per-entry notification settings.

    Empty strings mean "use the Companion app default" for sound/channel.
    """
    return {
        CONF_ACCESS_SOUND_IOS: "",
        CONF_ACCESS_CHANNEL_ANDROID: "",
        CONF_DOORBELL_SOUND_IOS: "",
        CONF_DOORBELL_CHANNEL_ANDROID: "",
        CONF_DOORBELL_KEY_CODE: DEFAULT_DOORBELL_KEY_CODE,
        CONF_DOORBELL_TARGETS: [],
    }


class DoormanStore:
    """Persists 2N UUID ↔ HA User ID mappings across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = _empty_data()

    async def async_load(self) -> None:
        """Load data from disk. Call once during integration setup.

        Stored data is layered over a fresh empty structure so a `.storage`
        file written by an older version gains any newly added top-level keys
        with safe defaults — no STORAGE_VERSION bump required for additive
        schema changes.
        """
        stored = await self._store.async_load()
        self._data = _empty_data()
        if stored:
            self._data.update(stored)

    # ------------------------------------------------------------------ #
    # Read                                                                 #
    # ------------------------------------------------------------------ #

    @property
    def user_links(self) -> dict[str, str]:
        """Return the full map of ``{two_n_uuid: ha_user_id}``."""
        return self._data.get("user_links", {})

    def get_ha_user_id(self, two_n_uuid: str) -> str | None:
        """Return the HA User ID linked to a 2N UUID, or None."""
        return self.user_links.get(two_n_uuid)

    def get_two_n_uuid(self, ha_user_id: str) -> str | None:
        """Return the 2N UUID linked to an HA User ID, or None."""
        return next(
            (uuid for uuid, uid in self.user_links.items() if uid == ha_user_id),
            None,
        )

    # ------------------------------------------------------------------ #
    # Write                                                                #
    # ------------------------------------------------------------------ #

    async def link_user(self, two_n_uuid: str, ha_user_id: str) -> None:
        """Link a 2N user to an HA user. Persists immediately."""
        self._data.setdefault("user_links", {})[two_n_uuid] = ha_user_id
        await self._store.async_save(self._data)

    async def unlink_user(self, two_n_uuid: str) -> None:
        """Remove the HA user link for a 2N UUID. Persists immediately."""
        self._data.get("user_links", {}).pop(two_n_uuid, None)
        await self._store.async_save(self._data)

    # ------------------------------------------------------------------ #
    # Last access times                                                    #
    # ------------------------------------------------------------------ #

    @property
    def last_access(self) -> dict[str, int]:
        """Return the full map of ``{two_n_uuid: utcTime}`` for last access."""
        return self._data.get("last_access", {})

    async def update_last_access(self, two_n_uuid: str, utc_time: int) -> None:
        """Record the most recent successful access time for a user. Persists immediately."""
        self._data.setdefault("last_access", {})[two_n_uuid] = utc_time
        await self._store.async_save(self._data)

    async def update_last_access_batch(
        self, entries: list[tuple[str, int]]
    ) -> None:
        """Record several access times and persist with a single disk write.

        A batch of access events arriving in one log pull would otherwise
        trigger one full-dict JSON serialization + disk write per event.
        """
        if not entries:
            return
        last_access = self._data.setdefault("last_access", {})
        for two_n_uuid, utc_time in entries:
            last_access[two_n_uuid] = utc_time
        await self._store.async_save(self._data)

    # ------------------------------------------------------------------ #
    # Notification targets                                                 #
    # ------------------------------------------------------------------ #

    @property
    def notification_targets(self) -> dict[str, list[str]]:
        """Return the full map of ``{two_n_uuid: [notify.* targets]}``."""
        return self._data.get("notification_targets", {})

    def get_notification_targets(self, two_n_uuid: str) -> list[str]:
        """Return the list of notify.* targets for a 2N UUID, or []."""
        return self.notification_targets.get(two_n_uuid, [])

    async def set_notification_targets(self, two_n_uuid: str, targets: list[str]) -> None:
        """Persist the notification targets for a 2N user."""
        self._data.setdefault("notification_targets", {})[two_n_uuid] = targets
        await self._store.async_save(self._data)

    async def clear_notification_targets(self, two_n_uuid: str) -> None:
        """Remove all notification targets for a 2N UUID. Persists immediately."""
        if self._data.get("notification_targets", {}).pop(two_n_uuid, None) is not None:
            await self._store.async_save(self._data)

    # ------------------------------------------------------------------ #
    # Per-entry notification settings                                      #
    # ------------------------------------------------------------------ #
    #
    # These live here rather than in ``entry.options`` for two reasons:
    #
    # 1. The options flow only shows ``poll_interval``, and
    #    ``async_create_entry(data=user_input)`` replaces the whole options
    #    dict — so opening Configure and pressing Submit would wipe every
    #    panel-set notification setting.
    # 2. Writing options fires the entry's update listener, which reloads the
    #    entry. A reload tears down the 2N log subscription, and a fresh
    #    subscription starts empty with no watermark, so events during the
    #    reload window are lost. Saving notification settings must not cost
    #    the user their doorbell events.
    #
    # Keyed by config ``entry_id``: the store is a single shared instance
    # across all entries, so a flat (unkeyed) dict would merge the settings of
    # every 2N device in a multi-device install.

    @property
    def notification_settings(self) -> dict[str, dict]:
        """Return the full map of ``{entry_id: settings}`` as stored."""
        return self._data.get("notification_settings", {})

    def get_notification_settings(self, entry_id: str) -> dict:
        """Return the notification settings for a config entry.

        Always returns every key: stored values are layered over the defaults,
        so callers never have to handle a missing field.
        """
        settings = _default_notification_settings()
        settings.update(self.notification_settings.get(entry_id, {}))
        # Hand back a copy of the mutable list so a caller can't mutate the
        # stored value without going through set_notification_settings.
        settings[CONF_DOORBELL_TARGETS] = list(settings[CONF_DOORBELL_TARGETS] or [])
        return settings

    async def set_notification_settings(self, entry_id: str, updates: dict) -> dict:
        """Merge ``updates`` into an entry's notification settings and persist.

        Partial updates are merged rather than replacing the whole dict so a
        caller that only owns some of the fields can't silently drop the rest.
        Returns the full effective settings after the merge.
        """
        settings = self.get_notification_settings(entry_id)
        settings.update(updates)
        settings[CONF_DOORBELL_TARGETS] = list(settings[CONF_DOORBELL_TARGETS] or [])
        self._data.setdefault("notification_settings", {})[entry_id] = settings
        await self._store.async_save(self._data)
        return self.get_notification_settings(entry_id)


# ---------------------------------------------------------------------- #
# Access log history                                                       #
# ---------------------------------------------------------------------- #


def _event_key(event: dict[str, Any]) -> str:
    """Return the dedupe key for a 2N log event.

    2N event ``id`` is a uint32 that restarts at 1 after every device reboot,
    so it is not unique on its own — pairing it with the event timestamp keeps
    a genuinely new post-reboot event (same id, later time) while still
    collapsing the same event seen twice (backfill overlapping the live feed,
    or a repeated backfill).
    """
    stamp = event.get("utcTime")
    if stamp is None:
        stamp = event.get("upTime")
    return f"{event.get('id')}@{stamp}"


def _event_time(event: dict[str, Any]) -> int:
    """Return an event's epoch-seconds timestamp, or 0 if it has none."""
    try:
        return int(event.get("utcTime") or 0)
    except (TypeError, ValueError):
        return 0


class AccessLogStore:
    """Durable access-log history for a single config entry.

    Events are kept oldest-first (which is the order the panel expects) and
    capped at ``max_events``, trimming the oldest. Writes are debounced via
    ``Store.async_delay_save`` so a burst of events from one log pull costs a
    single disk write; ``async_flush`` forces any pending write out on unload.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        max_events: int = MAX_STORED_LOG_EVENTS,
        save_delay: float = LOG_SAVE_DELAY,
    ) -> None:
        # One file per entry: a multi-device install must not merge histories,
        # and one device's events must not rewrite another's file.
        self._store: Store = Store(
            hass, LOG_STORAGE_VERSION, f"{LOG_STORAGE_KEY}.{entry_id}"
        )
        self._events: list[dict[str, Any]] = []
        self._keys: set[str] = set()
        self._max_events = max_events
        self._save_delay = save_delay
        self._dirty = False

    async def async_load(self) -> None:
        """Load the stored history from disk. Call once during setup."""
        stored = await self._store.async_load() or {}
        self._events = []
        self._keys = set()
        self._dirty = False
        # Route the stored events through _merge so a hand-edited or
        # older-format file still ends up deduped, sorted and capped.
        self._merge(stored.get("events") or [])

    @property
    def events(self) -> list[dict[str, Any]]:
        """Return the stored events, oldest first."""
        return self._events

    def add_events(self, events: list[dict[str, Any]]) -> int:
        """Merge events into the history and schedule a debounced write.

        Returns the number of events that were actually new. Adding only
        duplicates schedules no write at all.
        """
        added = self._merge(events)
        if added:
            self._dirty = True
            self._store.async_delay_save(self._data_to_save, self._save_delay)
        return added

    async def async_flush(self) -> None:
        """Write pending changes immediately (config entry unload / shutdown).

        HA flushes delayed saves itself on a clean stop, but not on a config
        entry reload — without this, events from the last debounce window
        would be lost on every reload.
        """
        if not self._dirty:
            return
        self._dirty = False
        await self._store.async_save(self._data_to_save())

    async def async_remove(self) -> None:
        """Delete the stored history (config entry removed)."""
        self._events = []
        self._keys = set()
        self._dirty = False
        await self._store.async_remove()

    def _data_to_save(self) -> dict[str, Any]:
        return {"events": self._events}

    def _merge(self, events: list[dict[str, Any]]) -> int:
        """Add unseen events, keep chronological order, trim the oldest."""
        fresh: list[dict[str, Any]] = []
        for event in events:
            if not isinstance(event, dict):
                continue
            key = _event_key(event)
            if key in self._keys:
                continue
            self._keys.add(key)
            fresh.append(event)
        if not fresh:
            return 0

        # A stable sort by timestamp interleaves backfilled history with
        # already-stored events correctly; events with equal (or missing)
        # timestamps keep their arrival order.
        merged = sorted(self._events + fresh, key=_event_time)
        overflow = len(merged) - self._max_events
        if overflow > 0:
            for event in merged[:overflow]:
                self._keys.discard(_event_key(event))
            merged = merged[overflow:]
        # Rebind rather than mutate: the previous list may be in flight to the
        # JSON encoder in an executor thread, and coordinator.data holds a
        # reference to it.
        self._events = merged
        return len(fresh)
