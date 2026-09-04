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

    @pytest.fixture(autouse=True)
    def _hide_aiohttp_shutdown_threads_on_floor():
        """Older PHACC rejects aiohttp's ``_run_safe_shutdown_loop`` leftovers.

        Current PHACC allowlists that thread name; PHACC 0.13.181 (HA floor)
        does not. Hide it from ``verify_cleanup``'s teardown check only — this
        fixture tears down before the plugin fixture, so the patch is in place
        for the assertion and restored at the start of the next test.
        """
        import inspect
        import threading

        import pytest_homeassistant_custom_component.plugins as plugins

        if "_run_safe_shutdown_loop" in inspect.getsource(plugins.verify_cleanup):
            yield
            return

        real_enumerate = threading.enumerate

        def filtered_enumerate():
            return [
                t
                for t in real_enumerate()
                if "_run_safe_shutdown_loop" not in t.name
            ]

        threading.enumerate = real_enumerate
        yield
        threading.enumerate = filtered_enumerate

    # ─── Representative fixture data ─────────────────────────────────────────────

    MOCK_DEVICE_INFO = {
        "deviceName": "2N IP Verso",
        "swVersion": "2.49.0.38",
        "serialNumber": "10-12345678",
        "hwVersion": "535v1",
    }
    # Sanitized MOCK_DEVICE_INFO["serialNumber"] — matches helpers.device_slug
    MOCK_DEVICE_SLUG = "1012345678"

    def doorman_eid(platform: str, object_id: str, slug: str = MOCK_DEVICE_SLUG) -> str:
        """Build a device-scoped Doorman entity ID for assertions."""
        return f"{platform}.doorman_{slug}_{object_id}"

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

    MOCK_CAMERA_CAPS = {
        "jpegResolution": [
            {"width": 320, "height": 240},
            {"width": 640, "height": 480},
        ]
    }

    MOCK_IO_PORTS = [
        {"port": "input1", "type": "input"},
        {"port": "relay1", "type": "output"},
    ]

    MOCK_IO_STATUS = [
        {"port": "input1", "state": 0},
        {"port": "relay1", "state": 0},
    ]

    MOCK_PHONE_ACCOUNTS = [
        {
            "account": 1,
            "accountType": "general",
            "enabled": True,
            "sipNumber": "1000",
            "registrationEnabled": True,
            "registered": True,
            "registerTime": 1743240000,
        },
    ]

    MOCK_SYSTEM_STATUS = {"systemTime": 1743242400, "upTime": 3600}

    # Smallest valid JPEG (1x1 white) for snapshot tests
    MOCK_JPEG = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000"
        "ffdb0043000302020302020303030304030304050805050404050a07070608"
        "0c0a0c0c0b0a0b0b0d0e12100d0e110e0b0b1016101113141515150c0f171816141812141514"
        "ffc0000b080001000101011100"
        "ffc4001f0000010501010101010100000000000000000102030405060708090a0b"
        "ffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191"
        "42a10815b1c109233352f0156272d10a162434e125f11718191a262728292a35363738393a434445464748"
        "494a535455565758595a636465666768696a737475767778797a838485868788898a9293949596979899"
        "9aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3"
        "e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9fa"
        "ffda0008010100003f00d2cf20ffd9"
    )

    # NB: 2N places identifiers flat on params (name/uuid) — there is no
    # nested "user" object. utcTime is epoch seconds (uint32) on a real
    # device, not an ISO string. See /api/log/pull output on a real device.
    MOCK_LOG_EVENTS = [
        {
            "id": "evt-001",
            "event": "UserAuthenticated",
            "utcTime": 1743242400,
            "params": {"ap": 0, "session": 1, "name": "Jane Doe", "uuid": "uuid-jane"},
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
        mock.get_switch_status = AsyncMock(return_value=[dict(s) for s in MOCK_SWITCHES])
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
        # Startup backfill: no on-box history by default. Tests that care
        # override this with their own historical events.
        mock.fetch_log_history = AsyncMock(return_value=[])
        mock.set_switch = AsyncMock(return_value=None)
        mock.get_switch_caps = AsyncMock(return_value=MOCK_SWITCHES)
        mock.create_user = AsyncMock(return_value={"uuid": "uuid-new", "name": "New User"})
        mock.update_user = AsyncMock(return_value=None)
        mock.delete_user = AsyncMock(return_value=None)
        mock.grant_access = AsyncMock(return_value=None)
        mock.get_call_status = AsyncMock(return_value=[])
        mock.hangup_call = AsyncMock(return_value=None)
        mock.hangup_all_calls = AsyncMock(return_value=0)
        mock.answer_call = AsyncMock(return_value=None)
        mock.answer_ringing_call = AsyncMock(return_value=False)
        mock.dial = AsyncMock(return_value=5)
        mock.get_camera_caps = AsyncMock(return_value=MOCK_CAMERA_CAPS)
        mock.get_camera_snapshot = AsyncMock(return_value=MOCK_JPEG)
        mock.get_io_caps = AsyncMock(return_value=MOCK_IO_PORTS)
        mock.get_io_status = AsyncMock(return_value=[dict(p) for p in MOCK_IO_STATUS])
        mock.get_phone_status = AsyncMock(return_value=[dict(a) for a in MOCK_PHONE_ACCOUNTS])
        mock.get_system_status = AsyncMock(return_value=dict(MOCK_SYSTEM_STATUS))
        mock.restart_device = AsyncMock(return_value=None)
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
    async def mock_frontend_setup(hass, request):
        """Mock HA HTTP and panel_custom calls.

        These require a live HTTP server which is not available in unit tests.
        The actual serving behaviour is tested in e2e / manual testing.

        Tests marked ``@pytest.mark.real_http`` opt out of the ``http`` mock and
        get the genuine component instead: ``hass_ws_client`` connects to
        ``hass.http.app``, so a MagicMock there makes the fixture unusable. Those
        tests exercise the WebSocket commands end-to-end (voluptuous schema
        validation included) rather than calling the handlers directly.
        """
        real_http = "real_http" in request.keywords

        # Mark frontend, panel_custom, and http as already set up so HA won't try
        # to load them (they require heavy optional deps not available in unit tests)
        mocked = ("frontend", "panel_custom") if real_http else ("frontend", "panel_custom", "http")
        for comp in mocked:
            if comp not in hass.config.components:
                mock_component(hass, comp)

        if real_http:
            from homeassistant.setup import async_setup_component

            assert await async_setup_component(hass, "http", {})
            with patch(
                "custom_components.doorman.panel_custom.async_register_panel",
                new=AsyncMock(),
            ):
                yield
            return

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

    # ─── Setup fixture ────────────────────────────────────────────────────────────

    @pytest.fixture
    async def setup_doorman(hass, doorman_config_entry, mock_2n_client):
        """Set up the Doorman integration and return the config entry."""
        doorman_config_entry.add_to_hass(hass)
        await hass.config_entries.async_setup(doorman_config_entry.entry_id)
        await hass.async_block_till_done()
        return doorman_config_entry

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
