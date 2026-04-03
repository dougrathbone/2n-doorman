"""Tests for the Doorman config flow (setup wizard)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.api_client import DoormanAuthError, DoormanConnectionError
from custom_components.doorman.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, DOMAIN

from .conftest import MOCK_DEVICE_INFO

VALID_INPUT = {
    CONF_HOST: "192.168.1.100",
    CONF_USERNAME: "admin",
    CONF_PASSWORD: "secret",
}


@pytest.mark.asyncio
async def test_config_flow_success(hass: HomeAssistant, mock_2n_client) -> None:
    """Happy path: valid credentials produce a config entry with the device name as title."""
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
async def test_options_flow_shows_current_interval(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Options flow form shows the current poll interval as default."""
    result = await hass.config_entries.options.async_init(setup_doorman.entry_id)

    assert result["type"] == "form"
    assert result["step_id"] == "init"


@pytest.mark.asyncio
async def test_options_flow_saves_poll_interval(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Options flow saves a new poll interval to entry.options."""
    result = await hass.config_entries.options.async_init(setup_doorman.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 60}
    )

    assert result["type"] == "create_entry"
    assert setup_doorman.options["poll_interval"] == 60


@pytest.mark.asyncio
async def test_reauth_flow_shows_form(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Re-auth flow shows a form pre-populated with the current username."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": setup_doorman.entry_id},
        data=setup_doorman.data,
    )

    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"


@pytest.mark.asyncio
async def test_reauth_flow_success(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Submitting valid credentials in the re-auth flow reloads the entry."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": setup_doorman.entry_id},
            data=setup_doorman.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "newpassword"},
        )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert setup_doorman.data[CONF_PASSWORD] == "newpassword"


@pytest.mark.asyncio
async def test_reauth_flow_invalid_auth(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Wrong credentials in re-auth show an error and keep the form open."""
    from custom_components.doorman.api_client import DoormanAuthError as _AuthError

    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(
            side_effect=_AuthError("bad creds")
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": setup_doorman.entry_id},
            data=setup_doorman.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: "wrongpassword"},
        )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "invalid_auth"


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
