"""Leader→follower user directory synchronisation for Doorman.

When two 2N devices are configured with leader/follower roles, this module
propagates user creates, updates, and deletes from the leader to the follower
on every successful leader poll cycle (~30 s).
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any

from homeassistant.core import CALLBACK_TYPE, HomeAssistant

from .const import CONF_SYNC_ROLE, CONF_SYNC_TARGET, DOMAIN, SYNC_ROLE_FOLLOWER
from .coordinator import DoormanCoordinator
from .storage import DoormanStore

_LOGGER = logging.getLogger(__name__)

# Fields that are synced from leader → follower
_SYNC_FIELDS = ("name", "pin", "card", "code", "validFrom", "validTo")


class UserSyncManager:
    """Propagates user directory changes from a leader device to its follower(s)."""

    def __init__(self, hass: HomeAssistant, store: DoormanStore) -> None:
        self._hass = hass
        self._store = store
        self._listeners: list[CALLBACK_TYPE] = []
        self._syncing = False
        self._initialized = False

    def async_setup(self) -> None:
        """Subscribe to leader coordinator updates to trigger sync."""
        pairs = self._find_sync_pairs()
        for leader_entry_id, follower_entry_id in pairs:
            leader_coord = self._get_coordinator(leader_entry_id)
            if leader_coord is None:
                continue
            unsub = leader_coord.async_add_listener(
                lambda lid=leader_entry_id, fid=follower_entry_id: (
                    self._hass.async_create_task(self._on_leader_update(lid, fid))
                )
            )
            self._listeners.append(unsub)
            _LOGGER.info(
                "User sync: %s (leader) → %s (follower)",
                leader_entry_id,
                follower_entry_id,
            )

    def async_teardown(self) -> None:
        """Remove all coordinator listeners."""
        for unsub in self._listeners:
            unsub()
        self._listeners.clear()
        self._initialized = False
        _LOGGER.debug("User sync manager torn down")

    # ------------------------------------------------------------------ #
    # Internal                                                             #
    # ------------------------------------------------------------------ #

    def _find_sync_pairs(self) -> list[tuple[str, str]]:
        """Return (leader_entry_id, follower_entry_id) pairs from config entries."""
        pairs: list[tuple[str, str]] = []
        for entry in self._hass.config_entries.async_entries(DOMAIN):
            if entry.options.get(CONF_SYNC_ROLE) == SYNC_ROLE_FOLLOWER:
                leader_entry_id = entry.options.get(CONF_SYNC_TARGET)
                if leader_entry_id:
                    pairs.append((leader_entry_id, entry.entry_id))
        return pairs

    def _get_coordinator(self, entry_id: str) -> DoormanCoordinator | None:
        return self._hass.data.get(DOMAIN, {}).get(entry_id)

    async def _on_leader_update(self, leader_entry_id: str, follower_entry_id: str) -> None:
        """Called after the leader coordinator successfully polls."""
        if self._syncing:
            _LOGGER.debug("Sync already in progress, skipping")
            return

        leader = self._get_coordinator(leader_entry_id)
        follower = self._get_coordinator(follower_entry_id)
        if leader is None or follower is None:
            return

        # Skip if follower has no data yet (e.g. still in error state)
        if follower.data is None:
            _LOGGER.debug("Follower has no data yet, skipping sync")
            return

        self._syncing = True
        try:
            mappings = self._store.sync_mappings
            if not mappings and not self._initialized:
                await self._initial_reconcile(leader, follower)
                self._initialized = True
            else:
                self._initialized = True
                await self._reconcile(leader, follower)
        except Exception:
            _LOGGER.exception("Error during user sync")
        finally:
            self._syncing = False

    async def _initial_reconcile(
        self, leader: DoormanCoordinator, follower: DoormanCoordinator
    ) -> None:
        """First-time setup: match users by name, build initial UUID mapping."""
        leader_users = leader.data.get("users", [])
        follower_users = follower.data.get("users", [])

        # Build name→user maps, skipping duplicate names
        leader_name_counts = Counter(u.get("name") for u in leader_users)
        follower_name_counts = Counter(u.get("name") for u in follower_users)

        follower_by_name: dict[str, dict] = {}
        for u in follower_users:
            name = u.get("name")
            if name and follower_name_counts[name] == 1:
                follower_by_name[name] = u

        mutated = False
        for lu in leader_users:
            name = lu.get("name")
            leader_uuid = lu.get("uuid")
            if not name or not leader_uuid:
                continue

            if leader_name_counts[name] > 1:
                _LOGGER.warning(
                    "Sync: duplicate name '%s' on leader — skipping auto-match", name
                )
                continue

            fu = follower_by_name.get(name)
            if fu:
                # Match found — create mapping and update follower if needed
                follower_uuid = fu.get("uuid")
                await self._store.set_sync_mapping(leader_uuid, follower_uuid)
                diff = self._compute_diff(lu, fu)
                if diff:
                    diff["uuid"] = follower_uuid
                    try:
                        await follower.client.update_user(diff)
                        mutated = True
                        _LOGGER.info("Sync: updated matched user '%s' on follower", name)
                    except Exception:
                        _LOGGER.exception("Sync: failed to update '%s' on follower", name)
            else:
                # No match — create on follower
                try:
                    payload = self._user_for_create(lu)
                    _LOGGER.debug("Sync: creating user on follower: %s", payload)
                    created = await follower.client.create_user(payload)
                    follower_uuid = created.get("uuid")
                    if follower_uuid:
                        await self._store.set_sync_mapping(leader_uuid, follower_uuid)
                    mutated = True
                    _LOGGER.info("Sync: created user '%s' on follower", name)
                except Exception:
                    _LOGGER.exception("Sync: failed to create '%s' on follower", name)

        if mutated:
            await follower.async_request_refresh()

    async def _reconcile(
        self, leader: DoormanCoordinator, follower: DoormanCoordinator
    ) -> None:
        """Incremental sync: compare leader state vs mappings, apply changes."""
        leader_users = leader.data.get("users", [])
        follower_users = follower.data.get("users", [])
        mappings = dict(self._store.sync_mappings)  # copy

        leader_by_uuid = {u["uuid"]: u for u in leader_users if "uuid" in u}
        follower_by_uuid = {u["uuid"]: u for u in follower_users if "uuid" in u}

        mutated = False

        # Creates: leader users not yet in mappings
        for leader_uuid, lu in leader_by_uuid.items():
            if leader_uuid in mappings:
                continue
            try:
                payload = self._user_for_create(lu)
                _LOGGER.debug("Sync: creating user on follower: %s", payload)
                created = await follower.client.create_user(payload)
                follower_uuid = created.get("uuid")
                if follower_uuid:
                    await self._store.set_sync_mapping(leader_uuid, follower_uuid)
                    mappings[leader_uuid] = follower_uuid
                mutated = True
                _LOGGER.info("Sync: created user '%s' on follower", lu.get("name"))
            except Exception:
                _LOGGER.exception(
                    "Sync: failed to create '%s' on follower", lu.get("name")
                )

        # Updates: mapped users with changed fields
        for leader_uuid, follower_uuid in list(mappings.items()):
            lu = leader_by_uuid.get(leader_uuid)
            fu = follower_by_uuid.get(follower_uuid)
            if lu is None or fu is None:
                continue
            diff = self._compute_diff(lu, fu)
            if diff:
                diff["uuid"] = follower_uuid
                try:
                    await follower.client.update_user(diff)
                    mutated = True
                    _LOGGER.info("Sync: updated user '%s' on follower", lu.get("name"))
                except Exception:
                    _LOGGER.exception(
                        "Sync: failed to update '%s' on follower", lu.get("name")
                    )

        # Deletes: mappings where leader user no longer exists
        for leader_uuid, follower_uuid in list(mappings.items()):
            if leader_uuid in leader_by_uuid:
                continue
            try:
                await follower.client.delete_user(follower_uuid)
                mutated = True
                _LOGGER.info("Sync: deleted user (leader uuid=%s) from follower", leader_uuid)
            except Exception:
                _LOGGER.exception(
                    "Sync: failed to delete user (leader uuid=%s) from follower",
                    leader_uuid,
                )
            await self._store.remove_sync_mapping(leader_uuid)

        if mutated:
            await follower.async_request_refresh()

    @staticmethod
    def _compute_diff(leader_user: dict[str, Any], follower_user: dict[str, Any]) -> dict[str, Any]:
        """Return a dict of fields that differ between leader and follower, or empty if identical."""
        diff: dict[str, Any] = {}
        for field in _SYNC_FIELDS:
            lv = leader_user.get(field)
            fv = follower_user.get(field)
            if lv != fv:
                diff[field] = lv
        return diff

    @staticmethod
    def _user_for_create(leader_user: dict[str, Any]) -> dict[str, Any]:
        """Build a user dict suitable for ``create_user`` from leader data."""
        return {field: leader_user[field] for field in _SYNC_FIELDS if field in leader_user}
