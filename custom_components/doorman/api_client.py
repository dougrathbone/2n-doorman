"""Async HTTP client for the 2N device REST API."""
from __future__ import annotations

import hashlib
import logging
import os
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
        scheme = "https" if use_ssl else "http"
        self._base_url = f"{scheme}://{host}"
        self._username = username
        self._password = password
        self._log_subscription_id: int | None = None

        # Build the SSL context once here (synchronous) so it is never created
        # inside the async event loop, which HA detects as a blocking call.
        if not use_ssl:
            self._ssl_ctx: ssl.SSLContext | bool | None = None
        elif verify_ssl:
            self._ssl_ctx = True  # use default context with full verification
        else:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_ctx = ctx

        # Plain session — we handle Digest auth manually in _request
        self._session = aiohttp.ClientSession()

    async def async_close(self) -> None:
        """Close the underlying session. Call from async_unload_entry."""
        if not self._session.closed:
            await self._session.close()

    def _ssl_context(self) -> ssl.SSLContext | bool | None:
        """Return the pre-built SSL context (or None for plain HTTP)."""
        return self._ssl_ctx

    def _build_digest_header(
        self,
        method: str,
        url: str,
        www_auth: str,
    ) -> str:
        """Compute an RFC 2617 Digest Authorization header value."""
        # Parse the WWW-Authenticate: Digest ... header
        params: dict[str, str] = {}
        # Strip the "Digest " prefix
        challenge = www_auth[len("Digest "):].strip()
        for part in challenge.split(","):
            part = part.strip()
            if "=" in part:
                key, _, val = part.partition("=")
                params[key.strip()] = val.strip().strip('"')

        realm = params.get("realm", "")
        nonce = params.get("nonce", "")
        qop = params.get("qop", "")
        algorithm = params.get("algorithm", "MD5").upper()

        # Extract the path from the URL for the digest uri
        from urllib.parse import urlparse
        parsed = urlparse(url)
        uri = parsed.path
        if parsed.query:
            uri += "?" + parsed.query

        def md5(s: str) -> str:
            return hashlib.md5(s.encode()).hexdigest()

        ha1 = md5(f"{self._username}:{realm}:{self._password}")
        ha2 = md5(f"{method.upper()}:{uri}")

        if qop in ("auth", "auth-int"):
            nc = "00000001"
            cnonce = os.urandom(8).hex()
            response = md5(f"{ha1}:{nonce}:{nc}:{cnonce}:{qop}:{ha2}")
            return (
                f'Digest username="{self._username}", realm="{realm}", '
                f'nonce="{nonce}", uri="{uri}", algorithm={algorithm}, '
                f'qop={qop}, nc={nc}, cnonce="{cnonce}", response="{response}"'
            )
        else:
            response = md5(f"{ha1}:{nonce}:{ha2}")
            return (
                f'Digest username="{self._username}", realm="{realm}", '
                f'nonce="{nonce}", uri="{uri}", algorithm={algorithm}, '
                f'response="{response}"'
            )

    async def _request(
        self,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/api/{endpoint}"
        ssl_ctx = self._ssl_context()
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            # First attempt — no auth header (some endpoints need no auth)
            async with self._session.request(
                method, url, params=params, json=json, ssl=ssl_ctx, timeout=timeout
            ) as resp:
                if resp.status == 401:
                    # Device may send multiple WWW-Authenticate headers (Basic + Digest).
                    # Find the Digest challenge; fall back to raising auth error if absent.
                    www_auth_values = resp.headers.getall("WWW-Authenticate", [])
                    digest_challenge = next(
                        (v for v in www_auth_values if v.startswith("Digest")), None
                    )
                    if digest_challenge is None:
                        raise DoormanAuthError("Invalid credentials")
                    # Second attempt with Digest auth
                    auth_header = self._build_digest_header(method, url, digest_challenge)
                    async with self._session.request(
                        method,
                        url,
                        params=params,
                        json=json,
                        ssl=ssl_ctx,
                        timeout=timeout,
                        headers={"Authorization": auth_header},
                    ) as resp2:
                        if resp2.status == 401:
                            raise DoormanAuthError("Invalid credentials")
                        if resp2.status == 403:
                            raise DoormanAuthError(
                                "Permission denied — ensure the API user has Directory access"
                            )
                        resp2.raise_for_status()
                        data: dict = await resp2.json()
                        if not data.get("success", True):
                            err = data.get("error", {})
                            raise DoormanApiError(
                                f"API error {err.get('code')}: {err.get('description', data)}"
                            )
                        return data

                if resp.status == 403:
                    raise DoormanAuthError(
                        "Permission denied — ensure the API user has Directory access"
                    )
                resp.raise_for_status()
                data = await resp.json()
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

    @staticmethod
    def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]:
        """Flatten 2N user record: promote access.{pin,card,code,validFrom,validTo} to top level."""
        user = dict(raw)
        access = user.pop("access", {}) or {}
        user.setdefault("pin", access.get("pin", ""))
        user.setdefault("card", access.get("card", []))
        user.setdefault("code", access.get("code", []))
        # validFrom/validTo are strings like "0" or ISO timestamp in the 2N API
        vf = access.get("validFrom")
        vt = access.get("validTo")
        user["validFrom"] = int(vf) if vf and vf != "0" else None
        user["validTo"] = int(vt) if vt and vt != "0" else None
        return user

    @staticmethod
    def _nest_user(flat: dict[str, Any]) -> dict[str, Any]:
        """Inverse of _flatten_user: build the nested access sub-object for dir/create and dir/update."""
        user = {k: v for k, v in flat.items() if k not in ("pin", "card", "code", "validFrom", "validTo")}
        access: dict[str, Any] = {}
        if "pin" in flat:
            access["pin"] = flat["pin"]
        if "card" in flat:
            access["card"] = flat["card"]
        if "code" in flat:
            access["code"] = flat["code"]
        if flat.get("validFrom"):
            access["validFrom"] = str(flat["validFrom"])
        if flat.get("validTo"):
            access["validTo"] = str(flat["validTo"])
        if access:
            user["access"] = access
        return user

    async def query_users(self) -> list[dict[str, Any]]:
        """Return all directory entries with credentials flattened to top-level fields."""
        data = await self._request("POST", "dir/query", json={})
        return [self._flatten_user(u) for u in data.get("result", {}).get("users", [])]

    async def get_user(self, uuid: str) -> dict[str, Any]:
        """Fetch a single user by UUID."""
        data = await self._request("POST", "dir/get", json={"uuid": uuid})
        return self._flatten_user(data.get("result", {}))

    async def create_user(self, user: dict[str, Any]) -> dict[str, Any]:
        """Create a new directory entry. Returns the record with server-assigned UUID."""
        data = await self._request("PUT", "dir/create", json={"user": self._nest_user(user)})
        return self._flatten_user(data.get("result", {}))

    async def update_user(self, user: dict[str, Any]) -> None:
        """Update an existing directory entry. ``user`` must include ``uuid``."""
        await self._request("PUT", "dir/update", json={"user": self._nest_user(user)})

    async def delete_user(self, uuid: str) -> None:
        """Delete a directory entry by UUID."""
        await self._request("PUT", "dir/delete", json={"uuid": uuid})

    # ------------------------------------------------------------------ #
    # Switches / relays (/api/switch/*)                                    #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_switch(sw: dict[str, Any]) -> dict[str, Any]:
        """Normalize the 2N switch dict: rename 'switch' key → 'id'."""
        result = dict(sw)
        if "switch" in result and "id" not in result:
            result["id"] = result.pop("switch")
        return result

    async def get_switch_caps(self) -> list[dict[str, Any]]:
        """List available switches and their capabilities."""
        data = await self._request("GET", "switch/caps")
        return [self._normalize_switch(s) for s in data.get("result", {}).get("switches", [])]

    async def get_switch_status(self) -> list[dict[str, Any]]:
        """Return current active/inactive state of all switches."""
        data = await self._request("GET", "switch/status")
        return [self._normalize_switch(s) for s in data.get("result", {}).get("switches", [])]

    async def set_switch(self, switch_id: int, action: str) -> None:
        """Control a switch. ``action``: ``'on'``, ``'off'``, or ``'trigger'``."""
        await self._request(
            "GET", "switch/ctrl", params={"switch": switch_id, "action": action}
        )

    # ------------------------------------------------------------------ #
    # Access points                                                        #
    # ------------------------------------------------------------------ #

    async def grant_access(self, access_point_id: int = 1, user_uuid: str | None = None) -> None:
        """Grant immediate access through an access point (bypasses credentials).

        ``user_uuid`` is required by most 2N firmware versions to attribute the
        access event in the device log.  Pass the 2N directory UUID of the user
        being let in.
        """
        params: dict[str, Any] = {"id": access_point_id}
        if user_uuid:
            params["user"] = user_uuid
        await self._request("GET", "accesspoint/grantaccess", params=params)

    # ------------------------------------------------------------------ #
    # Event log (/api/log/*)                                               #
    # ------------------------------------------------------------------ #

    async def _subscribe_log(self) -> int:
        """Create a log subscription and return the subscription ID."""
        data = await self._request("GET", "log/subscribe")
        sub_id: int = data["result"]["id"]
        self._log_subscription_id = sub_id
        return sub_id

    async def pull_log(self) -> list[dict[str, Any]]:
        """Pull log events since the last call (subscription-based, non-blocking).

        On the first call a subscription is created automatically.  If the
        device reports an invalid subscription ID (e.g. after a device
        reboot) the subscription is transparently renewed.
        """
        if self._log_subscription_id is None:
            await self._subscribe_log()

        try:
            data = await self._request(
                "GET", "log/pull",
                params={"id": self._log_subscription_id, "timeout": 0},
            )
        except DoormanApiError as err:
            # Error code 12 = invalid subscription id (subscription expired or device rebooted)
            if "12" in str(err):
                await self._subscribe_log()
                data = await self._request(
                    "GET", "log/pull",
                    params={"id": self._log_subscription_id, "timeout": 0},
                )
            else:
                raise

        return data.get("result", {}).get("events", [])
