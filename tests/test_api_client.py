"""Tests for TwoNApiClient — focusing on long-poll behaviour and timeout plumbing."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.doorman.api_client import (
    DoormanApiError,
    TwoNApiClient,
)


@pytest.fixture(autouse=True)
def _mock_session():
    """Patch aiohttp.ClientSession so tests never open real network connections.

    TwoNApiClient creates its own session in __init__; patching here ensures
    every test in this module gets a clean MagicMock instead of a real session,
    which prevents 'Unclosed client session' teardown errors.
    """
    mock = MagicMock()
    mock.closed = False
    mock.close = AsyncMock()
    with patch("custom_components.doorman.api_client.aiohttp.ClientSession", return_value=mock):
        yield mock


def _make_client() -> TwoNApiClient:
    return TwoNApiClient(
        session=MagicMock(),
        host="192.168.1.100",
        username="admin",
        password="secret",
        use_ssl=False,
    )


# ─── pull_log timeout plumbing ────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_pull_log_default_uses_zero_server_timeout() -> None:
    """pull_log() with no args passes timeout=0 to the device (non-blocking)."""
    client = _make_client()
    client._log_subscription_id = 42
    captured: dict = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["params"] = params
        captured["request_timeout"] = request_timeout
        return {"result": {"events": []}}

    client._request = fake_request
    await client.pull_log()

    assert captured["params"]["timeout"] == 0
    assert captured["request_timeout"] == 10  # max(10, 0+10)


@pytest.mark.asyncio
async def test_pull_log_long_poll_sets_server_and_client_timeout() -> None:
    """pull_log(server_timeout=20) sends timeout=20 and uses request_timeout=30."""
    client = _make_client()
    client._log_subscription_id = 42
    captured: dict = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["params"] = params
        captured["request_timeout"] = request_timeout
        return {"result": {"events": []}}

    client._request = fake_request
    await client.pull_log(server_timeout=20)

    assert captured["params"]["timeout"] == 20
    assert captured["request_timeout"] == 30  # 20 + 10


@pytest.mark.asyncio
async def test_pull_log_small_server_timeout_uses_minimum_client_timeout() -> None:
    """request_timeout is at least 10 even for tiny server_timeout values."""
    client = _make_client()
    client._log_subscription_id = 42
    captured: dict = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["request_timeout"] = request_timeout
        return {"result": {"events": []}}

    client._request = fake_request
    await client.pull_log(server_timeout=0)

    assert captured["request_timeout"] == 10


@pytest.mark.asyncio
async def test_pull_log_subscribes_on_first_call() -> None:
    """pull_log creates a subscription automatically when none exists."""
    client = _make_client()
    assert client._log_subscription_id is None

    subscribe_called = False

    async def fake_subscribe():
        nonlocal subscribe_called
        subscribe_called = True
        client._log_subscription_id = 99

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"result": {"events": []}}

    client._subscribe_log = fake_subscribe
    client._request = fake_request

    await client.pull_log()

    assert subscribe_called
    assert client._log_subscription_id == 99


@pytest.mark.asyncio
async def test_pull_log_renews_subscription_on_error_code_12() -> None:
    """An API error mentioning code 12 (invalid sub ID) triggers a re-subscribe."""
    client = _make_client()
    client._log_subscription_id = 1

    renew_called = False

    async def fake_subscribe():
        nonlocal renew_called
        renew_called = True
        client._log_subscription_id = 2

    call_count = 0

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise DoormanApiError("API error 12: invalid subscription id")
        return {"result": {"events": [{"event": "CardEntered"}]}}

    client._subscribe_log = fake_subscribe
    client._request = fake_request

    events = await client.pull_log()

    assert renew_called
    assert len(events) == 1
    assert events[0]["event"] == "CardEntered"


@pytest.mark.asyncio
async def test_pull_log_raises_non_12_errors() -> None:
    """API errors other than code 12 are propagated, not swallowed."""
    client = _make_client()
    client._log_subscription_id = 1

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        raise DoormanApiError("API error 5: something else")

    client._request = fake_request

    with pytest.raises(DoormanApiError, match="something else"):
        await client.pull_log()


@pytest.mark.asyncio
async def test_pull_log_returns_events_list() -> None:
    """pull_log returns the events array from the device response."""
    client = _make_client()
    client._log_subscription_id = 1

    fake_events = [
        {"event": "UserAuthenticated", "utcTime": "2026-01-01T00:00:00Z"},
        {"event": "CardEntered", "utcTime": "2026-01-01T00:00:01Z"},
    ]

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"result": {"events": fake_events}}

    client._request = fake_request
    result = await client.pull_log()

    assert result == fake_events


# ─── _request timeout param ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_request_passes_timeout_to_aiohttp() -> None:
    """_request constructs ClientTimeout using the request_timeout parameter."""
    import aiohttp

    client = _make_client()
    captured_timeouts: list = []

    with patch("custom_components.doorman.api_client.aiohttp.ClientTimeout",
               side_effect=lambda total: captured_timeouts.append(total) or aiohttp.ClientTimeout(total=total)):
        # Trigger a request — it will fail (mock session) but we only care about timeout
        try:
            await client._request("GET", "system/info", request_timeout=25)
        except Exception:
            pass

    assert 25 in captured_timeouts
