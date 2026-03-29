"""Async HTTP client for the 2N device REST API."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


class DoormanApiError(Exception):
    """Base 2N API error."""


class DoormanAuthError(DoormanApiError):
    """Authentication or permission error."""


class DoormanConnectionError(DoormanApiError):
    """Cannot reach the device."""


class TwoNApiClient:
    """Thin async wrapper around the 2N IP intercom HTTP API.

    Supports Basic Auth over HTTP or HTTPS. Configure the 2N device to use
    Basic Auth under Services → HTTP API.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        use_ssl: bool = False,
        verify_ssl: bool = True,
    ) -> None:
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}"
        self._session = session
        self._auth = aiohttp.BasicAuth(username, password)
        self._ssl: bool | None = verify_ssl if use_ssl else None

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/api/{endpoint}"
        try:
            async with self._session.request(
                method,
                url,
                auth=self._auth,
                params=params,
                json=json,
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    raise DoormanAuthError("Invalid credentials")
                if resp.status == 403:
                    raise DoormanAuthError(
                        "Permission denied — ensure the API user has Directory access"
                    )
                resp.raise_for_status()
                data: dict = await resp.json()
                if not data.get("success", True):
                    raise DoormanApiError(f"API returned failure: {data}")
                return data
        except aiohttp.ClientConnectorError as err:
            raise DoormanConnectionError(
                f"Cannot connect to {self._base_url}"
            ) from err
        except aiohttp.ClientError as err:
            raise DoormanApiError(f"Request failed: {err}") from err

    # ------------------------------------------------------------------ #
    # System                                                               #
    # ------------------------------------------------------------------ #

    async def get_system_info(self) -> dict[str, Any]:
        """Return device info: model, firmware version, serial number."""
        data = await self._request("GET", "system/info")
        return data.get("result", {})

    # ------------------------------------------------------------------ #
    # Directory — users & credentials (/api/dir/*)                        #
    # ------------------------------------------------------------------ #

    async def get_dir_template(self) -> dict[str, Any]:
        """Return the user record schema for this specific device model."""
        data = await self._request("GET", "dir/template")
        return data.get("result", {})

    async def query_users(self) -> list[dict[str, Any]]:
        """Return all directory entries (every configured user)."""
        data = await self._request("POST", "dir/query", json={})
        return data.get("result", {}).get("users", [])

    async def get_user(self, uuid: str) -> dict[str, Any]:
        """Fetch a single user by UUID."""
        data = await self._request("POST", "dir/get", json={"uuid": uuid})
        return data.get("result", {})

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """Create a new directory entry. Returns the record with server-assigned UUID."""
        data = await self._request("PUT", "dir/create", json={"user": user})
        return data.get("result", {})

    async def update_user(self, user: dict[str, Any]) -> None:
        """Update an existing directory entry. ``user`` must include ``uuid``."""
        await self._request("PUT", "dir/update", json={"user": user})

    async def delete_user(self, uuid: str) -> None:
        """Delete a directory entry by UUID."""
        await self._request("PUT", "dir/delete", json={"uuid": uuid})

    # ------------------------------------------------------------------ #
    # Switches / relays (/api/switch/*)                                    #
    # ------------------------------------------------------------------ #

    async def get_switch_caps(self) -> list[dict[str, Any]]:
        """List available switches and their capabilities."""
        data = await self._request("GET", "switch/caps")
        return data.get("result", {}).get("switches", [])

    async def get_switch_status(self) -> list[dict[str, Any]]:
        """Return current active/inactive state of all switches."""
        data = await self._request("GET", "switch/status")
        return data.get("result", {}).get("switches", [])

    async def set_switch(self, switch_id: int, action: str) -> None:
        """Control a switch. ``action``: ``'on'``, ``'off'``, or ``'trigger'``."""
        await self._request(
            "GET", "switch/ctrl", params={"switch": switch_id, "action": action}
        )

    # ------------------------------------------------------------------ #
    # Access points                                                        #
    # ------------------------------------------------------------------ #

    async def grant_access(self, access_point_id: int = 1) -> None:
        """Grant immediate access through an access point (bypasses credentials)."""
        await self._request(
            "GET",
            "accesspoint/grantaccess",
            params={"accessPointId": access_point_id},
        )

    # ------------------------------------------------------------------ #
    # Event log (/api/log/*)                                               #
    # ------------------------------------------------------------------ #

    async def pull_log(self, count: int = 100) -> list[dict[str, Any]]:
        """Pull the most recent log events (newest last)."""
        data = await self._request("GET", "log/pull", params={"count": count})
        return data.get("result", {}).get("events", [])
