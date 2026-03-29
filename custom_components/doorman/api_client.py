"""Async HTTP client for the 2N device REST API."""
from __future__ import annotations

import logging
import ssl
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
    """Async wrapper around the 2N IP intercom HTTP API.

    Uses Digest auth, which is required by 2N devices for directory (user)
    endpoints.  Directory operations also require HTTPS; this client always
    uses HTTPS with ``verify_ssl=False`` by default so that self-signed
    device certificates work out of the box on local networks.

    Non-directory endpoints (system/info, switch/*, log/*) work over plain
    HTTP with Digest auth as well, so we use HTTPS everywhere for simplicity.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,  # kept for interface compatibility; not used internally
        host: str,
        username: str,
        password: str,
        use_ssl: bool = True,
        verify_ssl: bool = False,
    ) -> None:
        # Always serve requests over HTTPS (required for directory endpoints)
        self._base_url = f"https://{host}"
        self._verify_ssl = verify_ssl

        # Create a dedicated session with Digest auth middleware.
        # DigestAuthMiddleware handles the 401 challenge automatically for
        # both MD5 and SHA variants used by 2N devices.
        self._digest_middleware = aiohttp.DigestAuthMiddleware(username, password)
        self._session = aiohttp.ClientSession(middlewares=[self._digest_middleware])

    async def async_close(self) -> None:
        """Close the underlying session. Call from async_unload_entry."""
        if not self._session.closed:
            await self._session.close()

    def _ssl_context(self) -> ssl.SSLContext | bool:
        """Return an SSL context or False to skip verification."""
        if self._verify_ssl:
            return True  # use default context with verification
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

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
                params=params,
                json=json,
                ssl=self._ssl_context(),
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
                    err = data.get("error", {})
                    raise DoormanApiError(
                        f"API error {err.get('code')}: {err.get('description', data)}"
                    )
                return data
        except aiohttp.ClientConnectorError as err:
            raise DoormanConnectionError(
                f"Cannot connect to {self._base_url}"
            ) from err
        except (DoormanApiError, DoormanAuthError, DoormanConnectionError):
            raise
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
