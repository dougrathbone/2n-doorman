"""Tests for Doorman push notification dispatch."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, DOMAIN
from custom_components.doorman.notifications import async_setup_notifications


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_notification_targets = MagicMock(return_value=["notify.mobile_app"])
    return store


async def test_notification_uses_config_entry_title_as_device_name(
    hass: HomeAssistant, mock_store
):
    """The message names the specific door via the config entry title."""
    hass.data[f"{DOMAIN}_store"] = mock_store

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="North Gate",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "admin", CONF_PASSWORD: "secret"},
    )
    entry.add_to_hass(hass)

    calls = []
    hass.services.async_register(
        "notify", "mobile_app",
        lambda call: calls.append(call),
    )

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "UserAuthenticated",
            "params": {"uuid": "uuid-abc", "name": "Jane"},
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["message"] == "Jane opened North Gate"
    assert calls[0].data["title"] == "Doorman"


async def test_notification_falls_back_when_entry_id_missing(hass: HomeAssistant, mock_store):
    """Without an entry_id (or with an unknown one), fall back to a generic message."""
    hass.data[f"{DOMAIN}_store"] = mock_store

    calls = []
    hass.services.async_register(
        "notify", "mobile_app",
        lambda call: calls.append(call),
    )

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "event_type": "UserAuthenticated",
            "params": {"uuid": "uuid-abc", "name": "Jane"},
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["message"] == "Jane opened the door"


async def test_no_notification_for_non_authenticated_events(hass: HomeAssistant, mock_store):
    """Events other than UserAuthenticated do not trigger notifications."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    mock_store.get_notification_targets.return_value = ["notify.mobile_app"]

    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "CardEntered", "params": {"uuid": "uuid-abc", "name": "Jane"}},
    )
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_no_notification_when_user_has_no_targets(hass: HomeAssistant, mock_store):
    """No notify calls when the user has no configured targets."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    mock_store.get_notification_targets.return_value = []

    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "UserAuthenticated", "params": {"uuid": "uuid-abc", "name": "Jane"}},
    )
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_notification_uses_fallback_name(hass: HomeAssistant, mock_store):
    """When user has no name, falls back to 'Someone'."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    mock_store.get_notification_targets.return_value = ["notify.mobile_app"]

    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "UserAuthenticated", "params": {"uuid": "uuid-abc"}},
    )
    await hass.async_block_till_done()

    assert calls[0].data["message"] == "Someone opened the door"


async def test_no_notification_when_store_missing(hass: HomeAssistant):
    """Gracefully skip when the store is not yet initialised."""
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "UserAuthenticated", "params": {"uuid": "uuid-abc", "name": "Jane"}},
    )
    await hass.async_block_till_done()

    assert len(calls) == 0


async def test_missing_notify_service_is_skipped(hass: HomeAssistant, mock_store, caplog):
    """A configured target whose notify service is gone is skipped with a warning.

    Previously the dispatch task called a nonexistent service and raised
    ServiceNotFound inside a task ("Task exception was never retrieved").
    """
    hass.data[f"{DOMAIN}_store"] = mock_store
    mock_store.get_notification_targets.return_value = ["notify.gone_service"]

    async_setup_notifications(hass)

    with caplog.at_level("WARNING", logger="custom_components.doorman.notifications"):
        hass.bus.async_fire(
            f"{DOMAIN}_access",
            {"event_type": "UserAuthenticated", "params": {"uuid": "uuid-abc", "name": "Jane"}},
        )
        await hass.async_block_till_done()

    assert "notify.gone_service is not registered" in caplog.text
    # No task exceptions leaked into the log
    assert "Task exception" not in caplog.text
