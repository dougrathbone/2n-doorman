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


def _raise_api_error(err: dict, raw: dict) -> None:
    """Raise the appropriate exception for a 2N API error response.

    Error code 10 is the standard "insufficient user privileges" error — the
    HTTP API user lacks System – Control privilege for directory write operations.
    """
    code = err.get("code")
    param = err.get("param", "")
    if code == 10:
        raise DoormanAuthError(
            "Directory write unavailable — the HTTP API user lacks System – Control "
            "privilege. In the 2N web UI go to Services → HTTP API → [username] and "
            "enable System API with Control (not just Monitoring) access."
        )
    param_info = f" (param={param!r})" if param else ""
    raise DoormanApiError(f"API error {code}{param_info}: {err.get('description', raw)}")


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

        # Build the SSL context once here so it is never re-created on each
        # request.  Critically, we avoid ssl.create_default_context() which
        # calls load_default_certs() / set_default_verify_paths() — blocking
        # file I/O that HA detects as a blocking call in the event loop.
        # ssl.SSLContext() does not load any certificates itself.
        if not use_ssl:
            self._ssl_ctx: ssl.SSLContext | bool | None = None
        elif verify_ssl:
            self._ssl_ctx = True  # let aiohttp use its default verified context
        else:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._ssl_ctx = ctx

        # Plain session — we handle Digest auth manually in _request
        self._session = aiohttp.ClientSession()
        # Number of access points on this device — loaded from dir/template at startup.
        # 2N devices control per-user enabled/disabled via access.accessPoints[N].enabled.
        self._access_point_count: int = 2

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
        request_timeout: int = 10,
    ) -> dict[str, Any]:
        url = f"{self._base_url}/api/{endpoint}"
        ssl_ctx = self._ssl_context()
        timeout = aiohttp.ClientTimeout(total=request_timeout)
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
                            _LOGGER.debug(
                                "%s %s failed — request body: %s — raw response: %s",
                                method, endpoint, json, data,
                            )
                            _raise_api_error(err, data)
                        return data

                if resp.status == 403:
                    raise DoormanAuthError(
                        "Permission denied — ensure the API user has Directory access"
                    )
                resp.raise_for_status()
                data = await resp.json()
                if not data.get("success", True):
                    err = data.get("error", {})
                    _LOGGER.debug(
                        "%s %s failed — request body: %s — raw response: %s",
                        method, endpoint, json, data,
                    )
                    _raise_api_error(err, data)
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

    async def check_directory_write_permission(self) -> bool:
        """Return True if the API user has directory write (create/update/delete) access.

        Sends a dir/update with a deliberately invalid UUID. The 2N device
        returns code 10 when the user lacks System – Control privilege; any
        other response (e.g. user-not-found) confirms that write access is granted.
        """
        try:
            await self._request(
                "PUT", "dir/update", json={"users": [{"uuid": "__permission_check__"}]}
            )
            return True  # unexpected success — write access confirmed
        except DoormanAuthError:
            return False  # code 10 → no System – Control privilege
        except DoormanApiError:
            return True  # any other API error → write access present, UUID just invalid

    async def load_dir_template(self) -> None:
        """Fetch the directory template and cache the number of access points for this device.

        Called once at startup so that _nest_user knows how many accessPoints entries
        to generate when enabling/disabling a user.  Failure is non-fatal — the default
        of 2 is used if the template cannot be read.
        """
        try:
            template = await self.get_dir_template()
            template_user = (template.get("users") or [{}])[0]
            ap_list = template_user.get("access", {}).get("accessPoints", [])
            if ap_list:
                self._access_point_count = len(ap_list)
                _LOGGER.debug("Doorman: device has %d access point(s)", self._access_point_count)
        except Exception:  # noqa: BLE001
            _LOGGER.debug(
                "Doorman: could not read dir/template; defaulting to %d access point(s)",
                self._access_point_count,
            )

    # ------------------------------------------------------------------ #
    # Directory — users & credentials (/api/dir/*)                        #
    # ------------------------------------------------------------------ #

    async def get_dir_template(self) -> dict[str, Any]:
        """Return the user record schema for this specific device model."""
        data = await self._request("GET", "dir/template")
        return data.get("result", {})

    @staticmethod
    def _flatten_user(raw: dict[str, Any]) -> dict[str, Any]:
        """Flatten 2N user record: promote access.{pin,card,code,validFrom,validTo} to top level.

        The 2N device controls enabled/disabled per access point via
        access.accessPoints[N].enabled — there is no top-level access.enabled field.
        A user is considered disabled only when ALL explicitly-configured access points
        are disabled; otherwise they are enabled.
        """
        user = dict(raw)
        access = user.pop("access", {}) or {}
        # Derive the logical enabled state from accessPoints
        access_points = access.get("accessPoints", [])
        configured = [ap for ap in access_points if isinstance(ap, dict)]
        if configured and all(ap.get("enabled", True) is False for ap in configured):
            user["enabled"] = False
        else:
            user["enabled"] = True
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
    def _nest_user(flat: dict[str, Any], access_point_count: int = 2) -> dict[str, Any]:
        """Inverse of _flatten_user: build the nested access sub-object for dir/create and dir/update.

        The 2N device does not have an access.enabled field.  Enable/disable is
        controlled per access point via access.accessPoints[N].enabled.  When the
        caller supplies an 'enabled' value we map it to all access points; omitting
        it leaves the device's existing accessPoints configuration untouched.
        """
        user = {k: v for k, v in flat.items() if k not in ("pin", "card", "code", "validFrom", "validTo", "enabled")}
        access: dict[str, Any] = {}
        if "enabled" in flat:
            # Set all access points to the same enabled state
            access["accessPoints"] = [{"enabled": flat["enabled"]} for _ in range(access_point_count)]
        if flat.get("pin"):
            access["pin"] = flat["pin"]
        if flat.get("card"):
            access["card"] = flat["card"]
        if flat.get("code"):
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
        data = await self._request(
            "PUT", "dir/create",
            json={"users": [self._nest_user(user, self._access_point_count)]},
        )
        created = (data.get("result", {}).get("users") or [{}])[0]
        return {**user, "uuid": created.get("uuid", "")}

    async def update_user(self, user: dict[str, Any]) -> None:
        """Update an existing directory entry. ``user`` must include ``uuid``."""
        payload = self._nest_user(user, self._access_point_count)
        _LOGGER.debug("dir/update payload: %s", payload)
        await self._request("PUT", "dir/update", json={"users": [payload]})

    async def delete_user(self, uuid: str) -> None:
        """Delete a directory entry by UUID."""
        await self._request("PUT", "dir/delete", json={"users": [{"uuid": uuid}]})

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

    async def get_access_point_caps(self) -> list[dict[str, Any]]:
        """Return the list of access points configured on this device.

        Each entry has at minimum an ``id`` (int) field.  The ``name`` field
        is populated if the device firmware supports it; fall back to a
        generated label if absent.
        """
        try:
            data = await self._request("GET", "accesspoint/caps")
            points = data.get("result", {}).get("accessPoints", [])
            return [
                {
                    "id": int(p.get("id", i + 1)),
                    "name": p.get("name") or f"Access point {p.get('id', i + 1)}",
                }
                for i, p in enumerate(points)
            ]
        except DoormanApiError:
            # Older firmware may not expose accesspoint/caps — return a single default point
            return [{"id": 1, "name": "Access point 1"}]

    # ------------------------------------------------------------------ #
    # Event log (/api/log/*)                                               #
    # ------------------------------------------------------------------ #

    async def _subscribe_log(self) -> int:
        """Create a log subscription and return the subscription ID."""
        data = await self._request("GET", "log/subscribe")
        sub_id: int = data["result"]["id"]
        self._log_subscription_id = sub_id
        return sub_id

    async def pull_log(self, server_timeout: int = 0) -> list[dict[str, Any]]:
        """Pull log events since the last call (subscription-based).

        ``server_timeout`` controls how long the 2N device holds the HTTP
        connection open waiting for events before returning an empty list.
        Use ``server_timeout=0`` for a non-blocking instant check (the
        original behaviour).  Use a value such as ``20`` for long-polling:
        the device returns as soon as any event arrives, or after 20 s with
        an empty list.  The client-side aiohttp timeout is set to
        ``server_timeout + 10`` so it always exceeds the server-side wait.
        """
        if self._log_subscription_id is None:
            await self._subscribe_log()

        client_timeout = max(10, server_timeout + 10)
        try:
            data = await self._request(
                "GET", "log/pull",
                params={"id": self._log_subscription_id, "timeout": server_timeout},
                request_timeout=client_timeout,
            )
        except DoormanApiError as err:
            # Error code 12 = invalid subscription id (subscription expired or device rebooted)
            if "12" in str(err):
                await self._subscribe_log()
                data = await self._request(
                    "GET", "log/pull",
                    params={"id": self._log_subscription_id, "timeout": server_timeout},
                    request_timeout=client_timeout,
                )
            else:
                raise

        return data.get("result", {}).get("events", [])
