"""Tests for TwoNApiClient — focusing on long-poll behaviour and timeout plumbing."""
from __future__ import annotations

import contextlib
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
        with contextlib.suppress(Exception):
            await client._request("GET", "system/info", request_timeout=25)

    assert 25 in captured_timeouts


# ═══════════════════════════════════════════════════════════════════════════════
# NEW TESTS — comprehensive coverage for api_client.py
# ═══════════════════════════════════════════════════════════════════════════════

from custom_components.doorman.api_client import (
    DoormanAuthError,
    DoormanConnectionError,
    _raise_api_error,
)

# ─── _raise_api_error ────────────────────────────────────────────────────────

def test_raise_api_error_code_10_raises_auth_error():
    """Error code 10 (insufficient privilege) should raise DoormanAuthError."""
    with pytest.raises(DoormanAuthError, match="System – Control"):
        _raise_api_error({"code": 10}, {})


def test_raise_api_error_other_code_raises_api_error():
    """Non-10 error codes raise DoormanApiError with description."""
    with pytest.raises(DoormanApiError, match="API error 5"):
        _raise_api_error({"code": 5, "description": "bad thing"}, {})


def test_raise_api_error_includes_param_info():
    """param field is included in the error message when present."""
    with pytest.raises(DoormanApiError, match="param='myfield'"):
        _raise_api_error({"code": 99, "param": "myfield", "description": "nope"}, {})


def test_raise_api_error_no_param():
    """When param is empty string, no param info is appended."""
    with pytest.raises(DoormanApiError, match=r"^API error 7:"):
        _raise_api_error({"code": 7, "param": "", "description": "oops"}, {})


# ─── _flatten_user ───────────────────────────────────────────────────────────

def test_flatten_user_full_access_data():
    """Full user record with access data is flattened correctly."""
    raw = {
        "uuid": "abc-123",
        "name": "Test User",
        "access": {
            "pin": "4567",
            "card": ["AABB"],
            "code": ["1111"],
            "validFrom": "1700000000",
            "validTo": "1800000000",
            "accessPoints": [{"enabled": True}, {"enabled": True}],
        },
    }
    result = TwoNApiClient._flatten_user(raw)
    assert result["uuid"] == "abc-123"
    assert result["pin"] == "4567"
    assert result["card"] == ["AABB"]
    assert result["code"] == ["1111"]
    assert result["validFrom"] == 1700000000
    assert result["validTo"] == 1800000000
    assert result["enabled"] is True
    assert "access" not in result


def test_flatten_user_empty_access():
    """User with empty access dict gets defaults."""
    raw = {"uuid": "u1", "name": "No Access", "access": {}}
    result = TwoNApiClient._flatten_user(raw)
    assert result["enabled"] is True  # no configured APs → enabled
    assert result["pin"] == ""
    assert result["card"] == []
    assert result["code"] == []
    assert result["validFrom"] is None
    assert result["validTo"] is None


def test_flatten_user_no_access_key():
    """User with no 'access' key at all gets defaults."""
    raw = {"uuid": "u2", "name": "Bare"}
    result = TwoNApiClient._flatten_user(raw)
    assert result["enabled"] is True
    assert result["pin"] == ""


def test_flatten_user_disabled_all_access_points():
    """User with all access points disabled is marked disabled."""
    raw = {
        "uuid": "u3",
        "access": {
            "accessPoints": [{"enabled": False}, {"enabled": False}],
        },
    }
    result = TwoNApiClient._flatten_user(raw)
    assert result["enabled"] is False


def test_flatten_user_mixed_enabled_disabled():
    """User with some enabled and some disabled access points is enabled."""
    raw = {
        "uuid": "u4",
        "access": {
            "accessPoints": [{"enabled": True}, {"enabled": False}],
        },
    }
    result = TwoNApiClient._flatten_user(raw)
    assert result["enabled"] is True


def test_flatten_user_access_points_default_enabled():
    """Access points without an explicit 'enabled' key default to True."""
    raw = {
        "uuid": "u5",
        "access": {
            "accessPoints": [{}],  # no 'enabled' key
        },
    }
    result = TwoNApiClient._flatten_user(raw)
    assert result["enabled"] is True


def test_flatten_user_valid_from_zero():
    """validFrom='0' should map to None."""
    raw = {"uuid": "u6", "access": {"validFrom": "0", "validTo": "0"}}
    result = TwoNApiClient._flatten_user(raw)
    assert result["validFrom"] is None
    assert result["validTo"] is None


def test_flatten_user_preserves_extra_fields():
    """Fields outside 'access' are preserved in the flattened output."""
    raw = {"uuid": "u7", "name": "Extra", "phone": "+1234", "access": {}}
    result = TwoNApiClient._flatten_user(raw)
    assert result["phone"] == "+1234"
    assert result["name"] == "Extra"


# ─── _nest_user ──────────────────────────────────────────────────────────────

def test_nest_user_enabled_true():
    """enabled=True generates accessPoints all set to True."""
    flat = {"uuid": "u1", "name": "Test", "enabled": True}
    result = TwoNApiClient._nest_user(flat, access_point_count=3)
    assert result["access"]["accessPoints"] == [
        {"enabled": True}, {"enabled": True}, {"enabled": True},
    ]
    assert "enabled" not in result  # stripped from top-level


def test_nest_user_enabled_false():
    """enabled=False generates accessPoints all set to False."""
    flat = {"uuid": "u1", "enabled": False}
    result = TwoNApiClient._nest_user(flat, access_point_count=2)
    assert result["access"]["accessPoints"] == [
        {"enabled": False}, {"enabled": False},
    ]


def test_nest_user_no_enabled_key():
    """When 'enabled' is not in flat, no accessPoints are generated."""
    flat = {"uuid": "u1", "pin": "1234"}
    result = TwoNApiClient._nest_user(flat)
    access = result.get("access", {})
    assert "accessPoints" not in access
    assert access.get("pin") == "1234"


def test_nest_user_with_credentials():
    """pin, card, code are placed inside access."""
    flat = {"uuid": "u1", "pin": "9999", "card": ["AB"], "code": ["55"]}
    result = TwoNApiClient._nest_user(flat)
    assert result["access"]["pin"] == "9999"
    assert result["access"]["card"] == ["AB"]
    assert result["access"]["code"] == ["55"]
    assert "pin" not in result
    assert "card" not in result
    assert "code" not in result


def test_nest_user_with_validity_dates():
    """validFrom/validTo are stringified and placed in access."""
    flat = {"uuid": "u1", "validFrom": 1700000000, "validTo": 1800000000}
    result = TwoNApiClient._nest_user(flat)
    assert result["access"]["validFrom"] == "1700000000"
    assert result["access"]["validTo"] == "1800000000"


def test_nest_user_empty_credentials_omitted():
    """Empty pin/card/code are not included in access."""
    flat = {"uuid": "u1", "pin": "", "card": [], "code": []}
    result = TwoNApiClient._nest_user(flat)
    assert "access" not in result  # nothing to nest


def test_nest_user_roundtrip():
    """Flattening then nesting should preserve the important fields."""
    raw = {
        "uuid": "rt-1",
        "name": "Roundtrip",
        "access": {
            "pin": "1234",
            "card": ["DEADBEEF"],
            "code": [],
            "validFrom": "1700000000",
            "validTo": "1800000000",
            "accessPoints": [{"enabled": True}, {"enabled": False}],
        },
    }
    flat = TwoNApiClient._flatten_user(raw)
    nested = TwoNApiClient._nest_user(flat, access_point_count=2)
    # enabled was True (mixed APs), so all APs become True
    assert nested["access"]["accessPoints"] == [{"enabled": True}, {"enabled": True}]
    assert nested["access"]["pin"] == "1234"
    assert nested["access"]["card"] == ["DEADBEEF"]
    assert nested["access"]["validFrom"] == "1700000000"
    assert nested["access"]["validTo"] == "1800000000"


# ─── _build_digest_header ───────────────────────────────────────────────────

def test_build_digest_header_with_qop_auth():
    """Digest header with qop=auth includes nc, cnonce, and qop fields."""
    client = _make_client()
    www_auth = 'Digest realm="2N", nonce="abc123", qop="auth", algorithm=MD5'
    header = client._build_digest_header("GET", "http://192.168.1.100/api/system/info", www_auth)
    assert header.startswith("Digest ")
    assert 'username="admin"' in header
    assert 'realm="2N"' in header
    assert 'nonce="abc123"' in header
    assert 'uri="/api/system/info"' in header
    assert "qop=auth" in header
    assert "nc=00000001" in header
    assert "cnonce=" in header
    assert "algorithm=MD5" in header
    assert 'response="' in header


def test_build_digest_header_without_qop():
    """Digest header without qop omits nc, cnonce, and qop fields."""
    client = _make_client()
    www_auth = 'Digest realm="device", nonce="xyz789", algorithm=MD5'
    header = client._build_digest_header("POST", "http://192.168.1.100/api/dir/query", www_auth)
    assert header.startswith("Digest ")
    assert 'username="admin"' in header
    assert 'realm="device"' in header
    assert 'uri="/api/dir/query"' in header
    assert "qop" not in header
    assert "nc=" not in header
    assert "cnonce" not in header
    assert 'response="' in header


def test_build_digest_header_with_query_string():
    """URI in digest header includes query string when present."""
    client = _make_client()
    www_auth = 'Digest realm="2N", nonce="n1", qop="auth"'
    header = client._build_digest_header(
        "GET", "http://192.168.1.100/api/switch/ctrl?switch=1&action=on", www_auth
    )
    assert 'uri="/api/switch/ctrl?switch=1&action=on"' in header


# ─── _request method ────────────────────────────────────────────────────────

def _make_response_mock(status=200, json_data=None, headers=None):
    """Create a mock aiohttp response as an async context manager."""
    resp = AsyncMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {"success": True})
    resp.raise_for_status = MagicMock()
    resp.headers = MagicMock()
    if headers:
        resp.headers.getall = MagicMock(return_value=headers.get("WWW-Authenticate", []))
    else:
        resp.headers.getall = MagicMock(return_value=[])
    # Make it work as an async context manager
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_request_success_no_auth_needed():
    """Successful request that returns 200 on the first attempt."""
    client = _make_client()
    resp_cm = _make_response_mock(200, {"success": True, "result": {"info": "ok"}})
    client._session.request = MagicMock(return_value=resp_cm)

    result = await client._request("GET", "system/info")
    assert result == {"success": True, "result": {"info": "ok"}}
    client._session.request.assert_called_once()


@pytest.mark.asyncio
async def test_request_401_retry_with_digest():
    """401 triggers digest auth retry; second attempt succeeds."""
    client = _make_client()

    # First call returns 401 with Digest challenge
    resp_401 = AsyncMock()
    resp_401.status = 401
    resp_401.headers = MagicMock()
    resp_401.headers.getall = MagicMock(return_value=[
        'Digest realm="2N", nonce="testnonce", qop="auth"'
    ])
    cm_401 = AsyncMock()
    cm_401.__aenter__ = AsyncMock(return_value=resp_401)
    cm_401.__aexit__ = AsyncMock(return_value=False)

    # Second call returns 200
    resp_200 = AsyncMock()
    resp_200.status = 200
    resp_200.json = AsyncMock(return_value={"success": True, "result": {}})
    resp_200.raise_for_status = MagicMock()
    cm_200 = AsyncMock()
    cm_200.__aenter__ = AsyncMock(return_value=resp_200)
    cm_200.__aexit__ = AsyncMock(return_value=False)

    client._session.request = MagicMock(side_effect=[cm_401, cm_200])

    result = await client._request("GET", "system/info")
    assert result == {"success": True, "result": {}}
    assert client._session.request.call_count == 2
    # Second call should have an Authorization header
    second_call_kwargs = client._session.request.call_args_list[1]
    assert "Authorization" in (second_call_kwargs.kwargs.get("headers") or {})


@pytest.mark.asyncio
async def test_request_401_no_digest_challenge_raises_auth_error():
    """401 without a Digest WWW-Authenticate header raises DoormanAuthError."""
    client = _make_client()
    resp_401 = AsyncMock()
    resp_401.status = 401
    resp_401.headers = MagicMock()
    resp_401.headers.getall = MagicMock(return_value=['Basic realm="device"'])
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=resp_401)
    cm.__aexit__ = AsyncMock(return_value=False)
    client._session.request = MagicMock(return_value=cm)

    with pytest.raises(DoormanAuthError, match="Invalid credentials"):
        await client._request("GET", "system/info")


@pytest.mark.asyncio
async def test_request_401_retry_still_401_raises_auth_error():
    """If the digest retry also returns 401, raise DoormanAuthError."""
    client = _make_client()

    resp_401_first = AsyncMock()
    resp_401_first.status = 401
    resp_401_first.headers = MagicMock()
    resp_401_first.headers.getall = MagicMock(return_value=[
        'Digest realm="2N", nonce="n1", qop="auth"'
    ])
    cm_first = AsyncMock()
    cm_first.__aenter__ = AsyncMock(return_value=resp_401_first)
    cm_first.__aexit__ = AsyncMock(return_value=False)

    resp_401_second = AsyncMock()
    resp_401_second.status = 401
    cm_second = AsyncMock()
    cm_second.__aenter__ = AsyncMock(return_value=resp_401_second)
    cm_second.__aexit__ = AsyncMock(return_value=False)

    client._session.request = MagicMock(side_effect=[cm_first, cm_second])

    with pytest.raises(DoormanAuthError, match="Invalid credentials"):
        await client._request("GET", "system/info")


@pytest.mark.asyncio
async def test_request_403_raises_auth_error():
    """403 on first attempt raises DoormanAuthError."""
    client = _make_client()
    resp_cm = _make_response_mock(403)
    client._session.request = MagicMock(return_value=resp_cm)

    with pytest.raises(DoormanAuthError, match="Permission denied"):
        await client._request("GET", "dir/query")


@pytest.mark.asyncio
async def test_request_403_on_digest_retry_raises_auth_error():
    """403 on the digest retry raises DoormanAuthError."""
    client = _make_client()

    # First call: 401 with Digest challenge
    resp_401 = AsyncMock()
    resp_401.status = 401
    resp_401.headers = MagicMock()
    resp_401.headers.getall = MagicMock(return_value=[
        'Digest realm="2N", nonce="n1", qop="auth"'
    ])
    cm_401 = AsyncMock()
    cm_401.__aenter__ = AsyncMock(return_value=resp_401)
    cm_401.__aexit__ = AsyncMock(return_value=False)

    # Second call: 403
    resp_403 = AsyncMock()
    resp_403.status = 403
    cm_403 = AsyncMock()
    cm_403.__aenter__ = AsyncMock(return_value=resp_403)
    cm_403.__aexit__ = AsyncMock(return_value=False)

    client._session.request = MagicMock(side_effect=[cm_401, cm_403])

    with pytest.raises(DoormanAuthError, match="Permission denied"):
        await client._request("GET", "dir/query")


@pytest.mark.asyncio
async def test_request_connection_error():
    """ClientConnectorError is wrapped in DoormanConnectionError."""
    import aiohttp
    client = _make_client()
    client._session.request = MagicMock(
        side_effect=aiohttp.ClientConnectorError(
            connection_key=MagicMock(), os_error=OSError("refused")
        )
    )

    with pytest.raises(DoormanConnectionError, match="Cannot connect"):
        await client._request("GET", "system/info")


@pytest.mark.asyncio
async def test_request_api_error_in_response():
    """success=false in JSON triggers _raise_api_error."""
    client = _make_client()
    resp_cm = _make_response_mock(200, {
        "success": False,
        "error": {"code": 5, "description": "something broke"},
    })
    client._session.request = MagicMock(return_value=resp_cm)

    with pytest.raises(DoormanApiError, match="something broke"):
        await client._request("GET", "dir/query")


@pytest.mark.asyncio
async def test_request_api_error_code_10_in_response():
    """success=false with error code 10 raises DoormanAuthError."""
    client = _make_client()
    resp_cm = _make_response_mock(200, {
        "success": False,
        "error": {"code": 10, "description": "no privilege"},
    })
    client._session.request = MagicMock(return_value=resp_cm)

    with pytest.raises(DoormanAuthError, match="System – Control"):
        await client._request("GET", "dir/update")


@pytest.mark.asyncio
async def test_request_generic_client_error():
    """Generic aiohttp.ClientError is wrapped in DoormanApiError."""
    import aiohttp
    client = _make_client()
    client._session.request = MagicMock(side_effect=aiohttp.ClientError("timeout"))

    with pytest.raises(DoormanApiError, match="Request failed"):
        await client._request("GET", "system/info")


# ─── check_directory_write_permission ────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_directory_write_permission_returns_false_on_auth_error():
    """DoormanAuthError (code 10) means no write permission → returns False."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        raise DoormanAuthError("no privilege")

    client._request = fake_request
    assert await client.check_directory_write_permission() is False


@pytest.mark.asyncio
async def test_check_directory_write_permission_returns_true_on_api_error():
    """DoormanApiError (non-10, e.g. user not found) means write OK → returns True."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        raise DoormanApiError("user not found")

    client._request = fake_request
    assert await client.check_directory_write_permission() is True


@pytest.mark.asyncio
async def test_check_directory_write_permission_returns_true_on_success():
    """Unexpected success still returns True."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True}

    client._request = fake_request
    assert await client.check_directory_write_permission() is True


# ─── get_system_info ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_system_info():
    """get_system_info extracts the result dict."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True, "result": {"model": "Verso", "fw": "2.49"}}

    client._request = fake_request
    result = await client.get_system_info()
    assert result == {"model": "Verso", "fw": "2.49"}


# ─── load_dir_template ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_load_dir_template_sets_access_point_count():
    """load_dir_template caches the number of access points from the template."""
    client = _make_client()

    async def fake_get_dir_template():
        return {
            "users": [{
                "access": {
                    "accessPoints": [{"enabled": True}, {"enabled": True}, {"enabled": True}],
                },
            }],
        }

    client.get_dir_template = fake_get_dir_template
    await client.load_dir_template()
    assert client._access_point_count == 3


@pytest.mark.asyncio
async def test_load_dir_template_failure_keeps_default():
    """If dir/template fails, the default access_point_count of 2 is kept."""
    client = _make_client()

    async def failing_template():
        raise DoormanApiError("not available")

    client.get_dir_template = failing_template
    await client.load_dir_template()
    assert client._access_point_count == 2


# ─── create_user ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_user_success():
    """create_user returns user dict with server-assigned UUID."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True, "result": {"users": [{"uuid": "new-uuid"}]}}

    client._request = fake_request
    result = await client.create_user({"name": "New User", "enabled": True})
    assert result["uuid"] == "new-uuid"
    assert result["name"] == "New User"


@pytest.mark.asyncio
async def test_create_user_empty_result_raises():
    """create_user raises DoormanApiError when device returns no users."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True, "result": {"users": []}}

    client._request = fake_request
    with pytest.raises(DoormanApiError, match="no user record"):
        await client.create_user({"name": "Fail"})


@pytest.mark.asyncio
async def test_create_user_none_users_raises():
    """create_user raises DoormanApiError when users key is None."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True, "result": {}}

    client._request = fake_request
    with pytest.raises(DoormanApiError, match="no user record"):
        await client.create_user({"name": "Fail"})


# ─── query_users ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_query_users_flattens_all():
    """query_users returns a list of flattened user dicts."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {
            "success": True,
            "result": {
                "users": [
                    {"uuid": "u1", "name": "A", "access": {"pin": "111", "accessPoints": [{"enabled": True}]}},
                    {"uuid": "u2", "name": "B", "access": {}},
                ],
            },
        }

    client._request = fake_request
    users = await client.query_users()
    assert len(users) == 2
    assert users[0]["pin"] == "111"
    assert users[1]["pin"] == ""


# ─── get_user ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_user_returns_flattened():
    """get_user returns a single flattened user dict."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {
            "success": True,
            "result": {"uuid": "u1", "name": "Jane", "access": {"pin": "9999"}},
        }

    client._request = fake_request
    user = await client.get_user("u1")
    assert user["pin"] == "9999"
    assert user["uuid"] == "u1"


# ─── update_user / delete_user ───────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_user_sends_nested_payload():
    """update_user nests the flat user data before sending."""
    client = _make_client()
    captured = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["json"] = json
        return {"success": True}

    client._request = fake_request
    await client.update_user({"uuid": "u1", "pin": "1234", "enabled": True})
    payload_user = captured["json"]["users"][0]
    assert "access" in payload_user
    assert payload_user["access"]["pin"] == "1234"


@pytest.mark.asyncio
async def test_delete_user_sends_uuid():
    """delete_user sends the UUID in the expected format."""
    client = _make_client()
    captured = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["endpoint"] = endpoint
        captured["json"] = json
        return {"success": True}

    client._request = fake_request
    await client.delete_user("u1")
    assert captured["endpoint"] == "dir/delete"
    assert captured["json"] == {"users": [{"uuid": "u1"}]}


# ─── Switch endpoints ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_switch_caps():
    """get_switch_caps normalizes switch key to id."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True, "result": {"switches": [{"switch": 1, "name": "Door"}]}}

    client._request = fake_request
    caps = await client.get_switch_caps()
    assert caps == [{"id": 1, "name": "Door"}]


@pytest.mark.asyncio
async def test_get_switch_status():
    """get_switch_status normalizes and returns switch states."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"success": True, "result": {"switches": [
            {"switch": 1, "active": False},
            {"switch": 2, "active": True},
        ]}}

    client._request = fake_request
    status = await client.get_switch_status()
    assert len(status) == 2
    assert status[0]["id"] == 1
    assert status[1]["active"] is True


@pytest.mark.asyncio
async def test_set_switch():
    """set_switch passes switch ID and action as params."""
    client = _make_client()
    captured = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["params"] = params
        return {"success": True}

    client._request = fake_request
    await client.set_switch(1, "trigger")
    assert captured["params"] == {"switch": 1, "action": "trigger"}


# ─── grant_access ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_grant_access_with_user_uuid():
    """grant_access passes access point ID and user UUID."""
    client = _make_client()
    captured = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["params"] = params
        return {"success": True}

    client._request = fake_request
    await client.grant_access(access_point_id=2, user_uuid="u1")
    assert captured["params"] == {"id": 2, "user": "u1"}


@pytest.mark.asyncio
async def test_grant_access_without_user_uuid():
    """grant_access without user_uuid omits the user param."""
    client = _make_client()
    captured = {}

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        captured["params"] = params
        return {"success": True}

    client._request = fake_request
    await client.grant_access(access_point_id=1)
    assert captured["params"] == {"id": 1}
    assert "user" not in captured["params"]


# ─── get_access_point_caps ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_access_point_caps_success():
    """get_access_point_caps returns normalized list of access points."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {
            "success": True,
            "result": {
                "accessPoints": [
                    {"id": 1, "name": "Front Door"},
                    {"id": 2, "name": "Side Gate"},
                ],
            },
        }

    client._request = fake_request
    points = await client.get_access_point_caps()
    assert points == [
        {"id": 1, "name": "Front Door"},
        {"id": 2, "name": "Side Gate"},
    ]


@pytest.mark.asyncio
async def test_get_access_point_caps_no_name_generates_label():
    """Access points without a name get a generated label."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {
            "success": True,
            "result": {"accessPoints": [{"id": 3}]},
        }

    client._request = fake_request
    points = await client.get_access_point_caps()
    assert points[0]["name"] == "Access point 3"


@pytest.mark.asyncio
async def test_get_access_point_caps_fallback_on_error():
    """Older firmware that doesn't support accesspoint/caps returns a default."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        raise DoormanApiError("not supported")

    client._request = fake_request
    points = await client.get_access_point_caps()
    assert points == [{"id": 1, "name": "Access point 1"}]


# ─── _subscribe_log ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscribe_log_stores_id():
    """_subscribe_log stores the subscription ID on the client."""
    client = _make_client()

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        return {"result": {"id": 77}}

    client._request = fake_request
    sub_id = await client._subscribe_log()
    assert sub_id == 77
    assert client._log_subscription_id == 77


# ─── pull_log with real _request mock (subscription creation) ────────────────

@pytest.mark.asyncio
async def test_pull_log_creates_subscription_then_pulls():
    """When no subscription exists, pull_log subscribes first then pulls."""
    client = _make_client()
    assert client._log_subscription_id is None

    call_log = []

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        call_log.append(endpoint)
        if endpoint == "log/subscribe":
            return {"result": {"id": 55}}
        return {"result": {"events": [{"event": "DoorOpened"}]}}

    client._request = fake_request
    events = await client.pull_log()
    assert call_log == ["log/subscribe", "log/pull"]
    assert events == [{"event": "DoorOpened"}]
    assert client._log_subscription_id == 55


@pytest.mark.asyncio
async def test_pull_log_error_12_resubscribes():
    """Error code 12 triggers re-subscribe, then retries pull."""
    client = _make_client()
    client._log_subscription_id = 10

    call_log = []
    pull_count = 0

    async def fake_request(method, endpoint, params=None, json=None, request_timeout=10):
        nonlocal pull_count
        call_log.append(endpoint)
        if endpoint == "log/subscribe":
            return {"result": {"id": 20}}
        pull_count += 1
        if pull_count == 1:
            raise DoormanApiError("API error 12: invalid subscription")
        return {"result": {"events": []}}

    client._request = fake_request
    events = await client.pull_log()
    assert call_log == ["log/pull", "log/subscribe", "log/pull"]
    assert events == []
    assert client._log_subscription_id == 20


# ─── SSL context construction ───────────────────────────────────────────────

def test_ssl_context_no_ssl():
    """use_ssl=False results in no SSL context."""
    client = TwoNApiClient(
        session=MagicMock(), host="h", username="u", password="p", use_ssl=False
    )
    assert client._ssl_context() is None


def test_ssl_context_verify_ssl_true():
    """verify_ssl=True returns True (let aiohttp use default)."""
    client = TwoNApiClient(
        session=MagicMock(), host="h", username="u", password="p",
        use_ssl=True, verify_ssl=True,
    )
    assert client._ssl_context() is True


def test_ssl_context_default_unverified():
    """Default (verify_ssl=False) creates SSLContext with check_hostname=False."""
    import ssl
    client = TwoNApiClient(
        session=MagicMock(), host="h", username="u", password="p",
        use_ssl=True, verify_ssl=False,
    )
    ctx = client._ssl_context()
    assert isinstance(ctx, ssl.SSLContext)
    assert ctx.check_hostname is False


# ─── _normalize_switch ───────────────────────────────────────────────────────

def test_normalize_switch_renames_key():
    """'switch' key is renamed to 'id'."""
    result = TwoNApiClient._normalize_switch({"switch": 1, "name": "Door"})
    assert result == {"id": 1, "name": "Door"}


def test_normalize_switch_keeps_id():
    """If 'id' already present, 'switch' is not renamed."""
    result = TwoNApiClient._normalize_switch({"id": 1, "switch": 1, "name": "Door"})
    assert result["id"] == 1
    assert "switch" in result  # preserved since id already exists
