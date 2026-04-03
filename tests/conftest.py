"""Shared test fixtures for Doorman unit tests."""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure the project root is first in sys.path so our custom_components is found
_PROJECT_ROOT = str(Path(__file__).parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

try:
    import pytest_homeassistant_custom_component as _pahcc  # noqa: F401
    _PAHCC_AVAILABLE = True
except ImportError:
    _PAHCC_AVAILABLE = False

if _PAHCC_AVAILABLE:
    from unittest.mock import AsyncMock, MagicMock, patch

    import pytest
    from pytest_homeassistant_custom_component.common import MockConfigEntry, mock_component

    # Ensure our custom_components directory is in the custom_components package path
    import custom_components as _cc_pkg

    _OUR_CC = str(Path(__file__).parent.parent / "custom_components")
    if _OUR_CC not in _cc_pkg.__path__:
        _cc_pkg.__path__.append(_OUR_CC)

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.doorman.const import (
        CONF_HOST,
        CONF_PASSWORD,
        CONF_USERNAME,
        DOMAIN,
    )

    pytest_plugins = "pytest_homeassistant_custom_component"

    @pytest.fixture(autouse=True)
    def enable_custom_integrations_fixture(enable_custom_integrations):  # noqa: F811
        """Enable discovery of custom integrations in tests."""

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
        mock.load_dir_template = AsyncMock(return_value=None)
        mock.check_directory_write_permission = AsyncMock(return_value=True)
        mock.get_access_point_caps = AsyncMock(return_value=[{"id": 1, "name": "Access point 1"}])
        mock.query_users = AsyncMock(return_value=MOCK_USERS)
        mock.get_switch_status = AsyncMock(return_value=MOCK_SWITCHES)
        # pull_log simulates long-poll: returns events on the first call, then
        # blocks indefinitely (mimicking the device holding the connection open).
        # Using asyncio.sleep inside the side_effect keeps the background task
        # alive but idle so it doesn't spin and interfere with test assertions.
        import asyncio as _asyncio
        _pull_log_calls = 0

        async def _pull_log_side_effect(server_timeout=0):
            nonlocal _pull_log_calls
            _pull_log_calls += 1
            if _pull_log_calls == 1:
                return MOCK_LOG_EVENTS
            await _asyncio.sleep(9999)
            return []

        mock.pull_log = _pull_log_side_effect
        mock.get_switch_caps = AsyncMock(return_value=MOCK_SWITCHES)
        mock.create_user = AsyncMock(return_value={"uuid": "uuid-new", "name": "New User"})
        mock.update_user = AsyncMock(return_value=None)
        mock.delete_user = AsyncMock(return_value=None)
        mock.grant_access = AsyncMock(return_value=None)
        mock.async_close = AsyncMock(return_value=None)

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
        # Mark frontend, panel_custom, and http as already set up so HA won't try
        # to load them (they require heavy optional deps not available in unit tests)
        for comp in ("frontend", "panel_custom", "http"):
            if comp not in hass.config.components:
                mock_component(hass, comp)

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

    # ─── Multi-device helpers ────────────────────────────────────────────────────

    def second_doorman_entry() -> MockConfigEntry:
        """Return a second MockConfigEntry with distinct connection details."""
        return MockConfigEntry(
            domain=DOMAIN,
            title="Second Device",
            data={
                CONF_HOST: "192.168.1.200",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "secret2",
            },
            unique_id="10-99999999",
        )

    async def setup_two_entries(hass, entry1):
        """Add two config entries to hass and ensure both are loaded."""
        entry2 = second_doorman_entry()
        entry1.add_to_hass(hass)
        entry2.add_to_hass(hass)
        await hass.config_entries.async_setup(entry1.entry_id)
        await hass.async_block_till_done()
        if entry2.state is not ConfigEntryState.LOADED:
            await hass.config_entries.async_setup(entry2.entry_id)
            await hass.async_block_till_done()
        return entry1, entry2
