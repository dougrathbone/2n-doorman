"""Tests for the Doorman config flow (setup wizard)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.api_client import DoormanAuthError, DoormanConnectionError
from custom_components.doorman.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SYNC_ROLE,
    CONF_SYNC_TARGET,
    CONF_USERNAME,
    DOMAIN,
    SYNC_ROLE_FOLLOWER,
    SYNC_ROLE_LEADER,
    SYNC_ROLE_NONE,
)

from .conftest import MOCK_DEVICE_INFO

VALID_INPUT = {
    CONF_HOST: "192.168.1.100",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
}

MOCK_DEVICE_INFO_2 = {
    "deviceName": "2N Access Unit M",
    "swVersion": "3.1.0",
    "serialNumber": "10-87654321",
    "hwVersion": "AU-M-v1",
}


@pytest.mark.asyncio
async def test_config_flow_success(hass: HomeAssistant, mock_2n_client) -> None:
    """Happy path: first device, no sync step — creates entry immediately."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_client = mock_cls.return_value
        mock_client.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        assert result["type"] == "form"
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], VALID_INPUT
        )

    assert result["type"] == "create_entry"
    assert result["title"] == MOCK_DEVICE_INFO["deviceName"]
    assert result["data"][CONF_HOST] == VALID_INPUT[CONF_HOST]


@pytest.mark.asyncio
async def test_config_flow_invalid_auth(hass: HomeAssistant) -> None:
    """Wrong credentials show an 'invalid_auth' error and keep the form open."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(
            side_effect=DoormanAuthError("bad creds")
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], VALID_INPUT
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


@pytest.mark.asyncio
async def test_config_flow_cannot_connect(hass: HomeAssistant) -> None:
    """Unreachable device shows a 'cannot_connect' error and keeps the form open."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(
            side_effect=DoormanConnectionError("timeout")
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], VALID_INPUT
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_config_flow_duplicate_device_aborts(hass: HomeAssistant, mock_2n_client) -> None:
    """Attempting to add the same device twice aborts with 'already_configured'."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)

        # First setup — succeeds
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(result["flow_id"], VALID_INPUT)

        # Second setup with the same serial — should abort
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], VALID_INPUT
        )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


# ─── Sync role during onboarding ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_config_flow_second_device_shows_sync_role(
    hass: HomeAssistant, mock_2n_client
) -> None:
    """When another doorman entry exists, adding a device shows the sync_role step."""
    # Pre-existing entry
    existing = MockConfigEntry(domain=DOMAIN, title="First Device", unique_id="10-11111111")
    existing.add_to_hass(hass)

    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO_2)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.200", CONF_USERNAME: "admin", CONF_PASSWORD: "pass"},
        )

    assert result["type"] == "form"
    assert result["step_id"] == "sync_role"


@pytest.mark.asyncio
async def test_config_flow_second_device_as_leader(
    hass: HomeAssistant, mock_2n_client
) -> None:
    """Setting sync_role=leader during onboarding creates entry with options."""
    existing = MockConfigEntry(domain=DOMAIN, title="First Device", unique_id="10-11111111")
    existing.add_to_hass(hass)

    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO_2)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.200", CONF_USERNAME: "admin", CONF_PASSWORD: "pass"},
        )
        assert result["step_id"] == "sync_role"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SYNC_ROLE: SYNC_ROLE_LEADER},
        )

    assert result["type"] == "create_entry"
    assert result["options"][CONF_SYNC_ROLE] == SYNC_ROLE_LEADER


@pytest.mark.asyncio
async def test_config_flow_second_device_no_sync(
    hass: HomeAssistant, mock_2n_client
) -> None:
    """Setting sync_role=none during onboarding creates entry without sync options."""
    existing = MockConfigEntry(domain=DOMAIN, title="First Device", unique_id="10-11111111")
    existing.add_to_hass(hass)

    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO_2)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.200", CONF_USERNAME: "admin", CONF_PASSWORD: "pass"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SYNC_ROLE: SYNC_ROLE_NONE},
        )

    assert result["type"] == "create_entry"
    assert result["options"] == {}


@pytest.mark.asyncio
async def test_config_flow_second_device_as_follower(
    hass: HomeAssistant, mock_2n_client
) -> None:
    """Full follower flow: credentials → sync_role → pick_leader → create entry."""
    existing = MockConfigEntry(
        domain=DOMAIN, title="Leader Device", unique_id="10-11111111",
        entry_id="leader_entry_id",
    )
    existing.add_to_hass(hass)

    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO_2)

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        # Step 1: credentials
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_HOST: "192.168.1.200", CONF_USERNAME: "admin", CONF_PASSWORD: "pass"},
        )
        assert result["step_id"] == "sync_role"

        # Step 2: pick follower role
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SYNC_ROLE: SYNC_ROLE_FOLLOWER},
        )
        assert result["step_id"] == "pick_leader"

        # Step 3: pick leader device
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SYNC_TARGET: "leader_entry_id"},
        )

    assert result["type"] == "create_entry"
    assert result["options"][CONF_SYNC_ROLE] == SYNC_ROLE_FOLLOWER
    assert result["options"][CONF_SYNC_TARGET] == "leader_entry_id"
