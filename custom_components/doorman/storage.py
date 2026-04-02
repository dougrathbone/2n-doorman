"""Persistent HA-side storage for Doorman.

Stores two kinds of per-user metadata:
  user_links          — 2N UUID → HA User ID (for identity linking)
  notification_targets — 2N UUID → list of notify.* service targets
"""
from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import STORAGE_KEY, STORAGE_VERSION

_EMPTY: dict = {"user_links": {}, "notification_targets": {}, "last_access": {}}


class DoormanStore:
    """Persists 2N UUID ↔ HA User ID mappings across restarts."""

    def __init__(self, hass: HomeAssistant) -> None:
        self._store: Store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._data: dict = dict(_EMPTY)

    async def async_load(self) -> None:
        """Load data from disk. Call once during integration setup."""
        stored = await self._store.async_load()
        self._data = stored or dict(_EMPTY)

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
    def last_access(self) -> dict[str, str]:
        """Return the full map of ``{two_n_uuid: utcTime}`` for last access."""
        return self._data.get("last_access", {})

    async def update_last_access(self, two_n_uuid: str, utc_time: str) -> None:
        """Record the most recent successful access time for a user. Persists immediately."""
        self._data.setdefault("last_access", {})[two_n_uuid] = utc_time
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
