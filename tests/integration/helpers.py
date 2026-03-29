"""Helper utilities for Doorman supervisor integration tests."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)


# ─── HA REST client ──────────────────────────────────────────────────────────

class HaClient:
    """Thin async wrapper around the HA REST and WebSocket APIs."""

    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> HaClient:
        self._session = aiohttp.ClientSession(headers=self._headers)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._session:
            await self._session.close()

    async def get(self, path: str) -> Any:
        assert self._session
        async with self._session.get(f"{self.base_url}{path}") as r:
            r.raise_for_status()
            return await r.json()

    async def post(self, path: str, data: dict | None = None) -> Any:
        assert self._session
        async with self._session.post(f"{self.base_url}{path}", json=data) as r:
            r.raise_for_status()
            return await r.json()

    async def get_state(self, entity_id: str) -> dict:
        return await self.get(f"/api/states/{entity_id}")

    async def call_service(self, domain: str, service: str, data: dict | None = None) -> None:
        await self.post(f"/api/services/{domain}/{service}", data or {})

    async def get_config_entries(self) -> list[dict]:
        return await self.get("/api/config/config_entries/entry")

    async def wait_for_state(
        self,
        entity_id: str,
        timeout: float = 30.0,
        interval: float = 1.0,
    ) -> dict:
        """Poll until the entity exists in HA state machine."""
        deadline = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < deadline:
            try:
                return await self.get_state(entity_id)
            except aiohttp.ClientResponseError as exc:
                if exc.status != 404:
                    raise
            await asyncio.sleep(interval)
        raise TimeoutError(f"Entity {entity_id!r} did not appear within {timeout}s")


# ─── HA WebSocket client ─────────────────────────────────────────────────────

class HaWebSocket:
    """Minimal HA WebSocket client for integration test assertions."""

    def __init__(self, base_url: str, token: str) -> None:
        ws_url = base_url.replace("http://", "ws://").replace("https://", "wss://")
        self._url = f"{ws_url}/api/websocket"
        self._token = token
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._session: aiohttp.ClientSession | None = None
        self._msg_id = 0

    async def connect(self) -> HaWebSocket:
        self._session = aiohttp.ClientSession()
        self._ws = await self._session.ws_connect(self._url)
        # HA sends auth_required on connect
        msg = await self._ws.receive_json()
        assert msg["type"] == "auth_required", f"Unexpected: {msg}"
        await self._ws.send_json({"type": "auth", "access_token": self._token})
        msg = await self._ws.receive_json()
        assert msg["type"] == "auth_ok", f"Auth failed: {msg}"
        return self

    async def close(self) -> None:
        if self._ws:
            await self._ws.close()
        if self._session:
            await self._session.close()

    async def command(self, msg_type: str, **kwargs: Any) -> Any:
        """Send a command and return the result payload."""
        self._msg_id += 1
        msg_id = self._msg_id
        await self._ws.send_json({"id": msg_id, "type": msg_type, **kwargs})
        while True:
            raw = await self._ws.receive_json()
            if raw.get("id") != msg_id:
                continue  # skip unrelated push messages
            if raw["type"] == "result":
                if not raw["success"]:
                    raise AssertionError(f"WS command {msg_type!r} failed: {raw['error']}")
                return raw.get("result")

    async def __aenter__(self) -> HaWebSocket:
        return await self.connect()

    async def __aexit__(self, *args: Any) -> None:
        await self.close()


# ─── Mock 2N admin client ────────────────────────────────────────────────────

class Mock2nAdmin:
    """Client for the mock 2N server's /admin/* test-assertion endpoints."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    async def get_calls(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.base_url}/admin/calls") as r:
                return (await r.json())["calls"]

    async def reset(self) -> None:
        async with aiohttp.ClientSession() as s:
            await s.post(f"{self.base_url}/admin/reset")

    async def get_users(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.base_url}/admin/users") as r:
                return (await r.json())["users"]

    async def get_switches(self) -> list[dict]:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{self.base_url}/admin/switches") as r:
                return (await r.json())["switches"]

    def calls_to(self, path: str) -> list[dict]:
        """Filter calls synchronously (after fetching with get_calls)."""
        raise NotImplementedError("Use async get_calls() then filter")


# ─── HA bootstrap helpers ────────────────────────────────────────────────────

async def wait_for_url(
    url: str,
    timeout: float = 180.0,
    interval: float = 3.0,
    expected_status: int = 200,
) -> None:
    """Poll until the URL returns an acceptable HTTP status."""
    deadline = asyncio.get_event_loop().time() + timeout
    async with aiohttp.ClientSession() as session:
        while asyncio.get_event_loop().time() < deadline:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                    if r.status == expected_status or r.status < 500:
                        return
            except Exception:
                pass
            await asyncio.sleep(interval)
    raise TimeoutError(f"URL {url!r} not ready after {timeout}s")


async def onboard_ha(ha_url: str) -> str:
    """Complete HA first-run onboarding and return a long-lived access token.

    This only works against a freshly started HA instance that has not yet
    been onboarded. In CI we start a clean container for each run.
    """
    async with aiohttp.ClientSession() as session:
        # Complete the 'user' onboarding step
        async with session.post(
            f"{ha_url}/api/onboarding/users",
            json={
                "client_id": f"{ha_url}/",
                "name": "Test Admin",
                "username": "testadmin",
                "password": "testpassword123",
                "language": "en",
            },
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()

        auth_code = data["auth_code"]

        # Exchange the one-time auth code for an access token
        async with session.post(
            f"{ha_url}/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": auth_code,
                "client_id": f"{ha_url}/",
            },
        ) as resp:
            resp.raise_for_status()
            tokens = await resp.json()

        short_token = tokens["access_token"]

        # Create a long-lived access token so subsequent requests don't expire
        async with session.post(
            f"{ha_url}/api/auth/long_lived_access_token",
            json={"client_name": "Doorman Integration Tests", "lifespan": 365},
            headers={"Authorization": f"Bearer {short_token}"},
        ) as resp:
            resp.raise_for_status()
            token_data = await resp.json()

        return token_data["token"]


async def install_doorman(ha_url: str, token: str, mock_2n_host: str, mock_2n_port: int = 8888) -> dict:
    """Create the Doorman config entry by walking the config flow via REST."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    async with aiohttp.ClientSession(headers=headers) as session:
        # Step 1: initialise the flow
        async with session.post(
            f"{ha_url}/api/config/config_entries/flow",
            json={"handler": "doorman"},
        ) as resp:
            resp.raise_for_status()
            flow = await resp.json()

        assert flow["type"] == "form", f"Expected form, got: {flow}"

        # Step 2: submit credentials (pointing at the mock 2N server)
        async with session.post(
            f"{ha_url}/api/config/config_entries/flow/{flow['flow_id']}",
            json={
                "host": f"{mock_2n_host}:{mock_2n_port}" if mock_2n_port != 80 else mock_2n_host,
                "username": "admin",
                "password": "admin",
                "use_ssl": False,
                "verify_ssl": False,
            },
        ) as resp:
            resp.raise_for_status()
            result = await resp.json()

        assert result["type"] == "create_entry", (
            f"Config flow did not complete successfully: {result}"
        )
        return result
