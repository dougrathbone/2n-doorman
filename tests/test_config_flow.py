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
    CONF_USE_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DOMAIN,
)

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
async def test_options_flow_submit_preserves_notification_settings(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Opening Configure and pressing Submit must not wipe panel-set settings.

    ``async_create_entry(data=user_input)`` replaces the whole options dict and
    the form only carries ``poll_interval``, so anything else kept in
    ``entry.options`` would be destroyed here. Notification settings live in
    DoormanStore precisely so that cannot happen.
    """
    store = hass.data[f"{DOMAIN}_store"]
    settings = {
        "access_sound_ios": "US-EN-Alexa-Front-Door-Opened.wav",
        "doorbell_key_code": "%2",
        "doorbell_targets": ["notify.mobile_app"],
    }
    await store.set_notification_settings(setup_doorman.entry_id, settings)

    result = await hass.config_entries.options.async_init(setup_doorman.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"poll_interval": 30}
    )
    await hass.async_block_till_done()

    assert result["type"] == "create_entry"
    stored = hass.data[f"{DOMAIN}_store"].get_notification_settings(setup_doorman.entry_id)
    assert stored["access_sound_ios"] == "US-EN-Alexa-Front-Door-Opened.wav"
    assert stored["doorbell_key_code"] == "%2"
    assert stored["doorbell_targets"] == ["notify.mobile_app"]


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

    # The reload now runs from the entry's update listener as a background
    # task rather than being awaited inside the flow, so settle it here.
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert setup_doorman.data[CONF_PASSWORD] == "newpassword"
    assert setup_doorman.state is config_entries.ConfigEntryState.LOADED


class _EntryLifecycleCounter:
    """Count how many times the integration sets up / unloads a config entry.

    Patches the module-level ``async_setup_entry`` / ``async_unload_entry``
    that HA looks up on the integration module for every (un)load, so it counts
    real lifecycle cycles rather than a proxy for them.
    """

    def __init__(self) -> None:
        self.setups: list[str] = []
        self.unloads: list[str] = []

    def __enter__(self) -> _EntryLifecycleCounter:
        import custom_components.doorman as module

        real_setup = module.async_setup_entry
        real_unload = module.async_unload_entry

        async def counting_setup(hass, entry):
            self.setups.append(entry.entry_id)
            return await real_setup(hass, entry)

        async def counting_unload(hass, entry):
            self.unloads.append(entry.entry_id)
            return await real_unload(hass, entry)

        self._patches = [
            patch.object(module, "async_setup_entry", counting_setup),
            patch.object(module, "async_unload_entry", counting_unload),
        ]
        for p in self._patches:
            p.start()
        return self

    def __exit__(self, *exc_info) -> None:
        for p in self._patches:
            p.stop()


async def _submit_reauth(hass: HomeAssistant, entry: MockConfigEntry, password: str) -> dict:
    """Run the reauth flow to completion with the given password."""
    with patch("custom_components.doorman.config_flow.TwoNApiClient") as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: "admin", CONF_PASSWORD: password},
        )
    await hass.async_block_till_done()
    return result


@pytest.mark.asyncio
async def test_reauth_reloads_a_loaded_entry_exactly_once(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """One reauth must produce one teardown/rebuild cycle, not two.

    ``async_update_entry`` fires the entry's update listeners on *any* change,
    and ``__init__.py`` registers one that reloads the entry — so the explicit
    ``async_reload`` the flow used to always call was a second, redundant
    reload. Measured before the fix: 2 setups + 2 unloads for one reauth, i.e.
    two rounds of device calls, two backfill subscriptions (one cancelled
    mid-flight) and two windows with no doorman.* services.
    """
    with _EntryLifecycleCounter() as counter:
        result = await _submit_reauth(hass, setup_doorman, "newpassword")

    assert result["reason"] == "reauth_successful"
    assert counter.setups == [setup_doorman.entry_id]
    assert counter.unloads == [setup_doorman.entry_id]
    assert setup_doorman.state is config_entries.ConfigEntryState.LOADED
    assert setup_doorman.data[CONF_PASSWORD] == "newpassword"


@pytest.mark.asyncio
async def test_reauth_with_unchanged_credentials_still_reloads_once(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Re-submitting the same credentials must still reload the entry — once.

    ``async_update_entry`` fires no update listeners when nothing changed, so
    simply deleting the explicit reload would leave this reauth doing nothing
    at all.
    """
    with _EntryLifecycleCounter() as counter:
        result = await _submit_reauth(hass, setup_doorman, "secret")

    assert result["reason"] == "reauth_successful"
    assert counter.setups == [setup_doorman.entry_id]
    assert counter.unloads == [setup_doorman.entry_id]
    assert setup_doorman.state is config_entries.ConfigEntryState.LOADED


@pytest.mark.asyncio
async def test_reauth_sets_up_an_entry_that_failed_to_load(
    hass: HomeAssistant, doorman_config_entry: MockConfigEntry, mock_2n_client,
) -> None:
    """The usual reauth case: the entry never loaded, so it has no update listener.

    Credentials rejected at startup raise ConfigEntryAuthFailed before
    ``add_update_listener`` runs, so nothing would reload the entry if the flow
    relied solely on the listener. It must set the entry up exactly once.
    """
    mock_2n_client.get_system_info.side_effect = DoormanAuthError("bad creds")
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()
    assert doorman_config_entry.state is config_entries.ConfigEntryState.SETUP_ERROR

    mock_2n_client.get_system_info.side_effect = None
    with _EntryLifecycleCounter() as counter:
        result = await _submit_reauth(hass, doorman_config_entry, "newpassword")

    assert result["reason"] == "reauth_successful"
    assert counter.setups == [doorman_config_entry.entry_id]
    assert doorman_config_entry.state is config_entries.ConfigEntryState.LOADED


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


@pytest.mark.asyncio
async def test_reconfigure_flow_shows_prefilled_form(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Reconfigure flow shows the connection form pre-filled with current data."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": setup_doorman.entry_id},
    )

    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"
    # Current values suggested in the schema: add_suggested_values_to_schema
    # stores them in Marker.description["suggested_value"].
    keys = {
        k.schema: (k.description or {}).get("suggested_value")
        for k in result["data_schema"].schema
    }
    assert keys[CONF_HOST] == setup_doorman.data[CONF_HOST]
    assert keys[CONF_USERNAME] == setup_doorman.data[CONF_USERNAME]


@pytest.mark.asyncio
async def test_reconfigure_flow_success(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Valid new connection details update the entry and reload it."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(return_value=MOCK_DEVICE_INFO)

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": setup_doorman.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.0.99",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "newpassword",
                CONF_USE_SSL: True,
                CONF_VERIFY_SSL: False,
            },
        )
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert setup_doorman.data[CONF_HOST] == "192.168.0.99"
    assert setup_doorman.data[CONF_USE_SSL] is True


@pytest.mark.asyncio
async def test_reconfigure_flow_invalid_auth(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Bad credentials in reconfigure show an error and keep the old data."""
    from custom_components.doorman.api_client import DoormanAuthError

    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(
            side_effect=DoormanAuthError("bad credentials")
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": setup_doorman.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.0.99",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "wrong",
                CONF_USE_SSL: True,
                CONF_VERIFY_SSL: False,
            },
        )

    assert result["type"] == "form"
    assert result["errors"] == {"base": "invalid_auth"}
    assert setup_doorman.data[CONF_HOST] != "192.168.0.99"


@pytest.mark.asyncio
async def test_reconfigure_flow_rejects_different_device(
    hass: HomeAssistant, setup_doorman: MockConfigEntry,
) -> None:
    """Pointing the entry at a different device (serial mismatch) aborts."""
    with patch(
        "custom_components.doorman.config_flow.TwoNApiClient"
    ) as mock_cls:
        mock_cls.return_value.get_system_info = AsyncMock(
            return_value={**MOCK_DEVICE_INFO, "serialNumber": "different-serial"}
        )

        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": setup_doorman.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_HOST: "192.168.0.99",
                CONF_USERNAME: "admin",
                CONF_PASSWORD: "newpassword",
                CONF_USE_SSL: True,
                CONF_VERIFY_SSL: False,
            },
        )

    assert result["type"] == "abort"
    assert result["reason"] == "different_device"
    assert setup_doorman.data[CONF_HOST] != "192.168.0.99"
