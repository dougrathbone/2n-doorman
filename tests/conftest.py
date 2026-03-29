"""Shared test fixtures for Doorman integration tests."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

# ─── Representative fixture data ─────────────────────────────────────────────

MOCK_DEVICE_INFO = {
    "deviceName": "2N IP Verso",
    "swVersion": "2.49.0.38",
    "serialNumber": "10-12345678",
    "hwVersion": "535v1",
}

MOCK_USERS = [
    {
        "uuid": "uuid-jane",
        "name": "Jane Doe",
        "pin": "1234",
        "card": ["AABBCCDD"],
        "code": [],
        "validFrom": None,
        "validTo": None,
    },
    {
        "uuid": "uuid-john",
        "name": "John Smith",
        "pin": "",
        "card": [],
        "code": ["9999"],
        "validFrom": None,
        "validTo": None,
    },
]

MOCK_SWITCHES = [
    {"id": 1, "name": "Main Door", "active": False},
]

MOCK_LOG_EVENTS = [
    {
        "id": "evt-001",
        "event": "UserAuthenticated",
        "utcTime": "2026-03-29T10:00:00Z",
        "params": {"user": {"name": "Jane Doe", "id": "uuid-jane"}, "valid": True},
    },
]

# ─── Config entry factory ─────────────────────────────────────────────────────

@pytest.fixture
def doorman_config_entry() -> MockConfigEntry:
    """Return a pre-built MockConfigEntry for Doorman."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="2N IP Verso",
        data={
            CONF_HOST: "192.168.1.100",
            CONF_USERNAME: "admin",
            CONF_PASSWORD: "secret",
        },
        unique_id=MOCK_DEVICE_INFO["serialNumber"],
    )


# ─── API client mock ──────────────────────────────────────────────────────────

@pytest.fixture
def mock_2n_client():
    """Patch TwoNApiClient with a mock returning fixture data.

    Patches at the point of use (__init__.py import) so coordinator
    and config_flow both get the same mock.
    """
    mock = MagicMock()
    mock.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)
    mock.query_users = AsyncMock(return_value=MOCK_USERS)
    mock.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
    mock.pull_log = AsyncMock(return_value=MOCK_LOG_EVENTS)
    mock.get_switch_caps = AsyncMock(return_value=MOCK_SWITCHES)
    mock.create_user = AsyncMock(return_value={"uuid": "uuid-new", "name": "New User"})
    mock.update_user = AsyncMock(return_value=None)
    mock.delete_user = AsyncMock(return_value=None)
    mock.grant_access = AsyncMock(return_value=None)

    with patch(
        "custom_components.doorman.TwoNApiClient", return_value=mock
    ) as patched, patch(
        "custom_components.doorman.config_flow.TwoNApiClient", return_value=mock
    ):
        patched.return_value = mock
        yield mock


# ─── HA frontend / HTTP mock ──────────────────────────────────────────────────

@pytest.fixture(autouse=True)
async def mock_frontend_setup(hass):
    """Mock HA HTTP and panel_custom calls.

    These require a live HTTP server which is not available in unit tests.
    The actual serving behaviour is tested in e2e / manual testing.
    """
    mock_http = MagicMock()
    mock_http.async_register_static_paths = AsyncMock()

    with (
        patch(
            "custom_components.doorman.panel_custom.async_register_panel",
            new=AsyncMock(),
        ),
        patch.object(hass, "http", mock_http, create=True),
    ):
        yield
