"""Persistent HA-side storage for Doorman.

Stores per-user metadata:
  user_links          — 2N UUID → HA User ID (for identity linking)
  notification_targets — 2N UUID → list of notify.* service targets
  sync_mappings       — leader UUID → follower UUID (for cross-device sync)
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

_EMPTY: dict = {"user_links": {}, "notification_targets": {}, "sync_mappings": {}}


class DoormanStore:
    """Persists 2N UUID ↔ HA User ID mappings across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = dict(_EMPTY)

    async def async_load(self) -> None:
        """Load data from disk. Call once during integration setup."""
        stored = await self._store.async_load()
        if stored is None:
            self._data = dict(_EMPTY)
        else:
            self._data = stored
            # Migrate from v1: add sync_mappings if missing
            if "sync_mappings" not in self._data:
                self._data["sync_mappings"] = {}
                await self._store.async_save(self._data)

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

    # ------------------------------------------------------------------ #
    # Sync mappings (leader UUID → follower UUID)                          #
    # ------------------------------------------------------------------ #

    @property
    def sync_mappings(self) -> dict[str, str]:
        """Return the full map of ``{leader_uuid: follower_uuid}``."""
        return self._data.get("sync_mappings", {})

    async def set_sync_mapping(self, leader_uuid: str, follower_uuid: str) -> None:
        """Store a leader→follower UUID mapping. Persists immediately."""
        self._data.setdefault("sync_mappings", {})[leader_uuid] = follower_uuid
        await self._store.async_save(self._data)

    async def remove_sync_mapping(self, leader_uuid: str) -> None:
        """Remove a leader→follower UUID mapping. Persists immediately."""
        self._data.get("sync_mappings", {}).pop(leader_uuid, None)
        await self._store.async_save(self._data)

    def get_leader_uuid_for_follower(self, follower_uuid: str) -> str | None:
        """Reverse lookup: return the leader UUID for a given follower UUID, or None."""
        return next(
            (lid for lid, fid in self.sync_mappings.items() if fid == follower_uuid),
            None,
        )

    async def clear_sync_mappings(self) -> None:
        """Remove all sync mappings. Persists immediately."""
        self._data["sync_mappings"] = {}
        await self._store.async_save(self._data)
