"""Tests for Doorman push notification dispatch."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant

from custom_components.doorman.const import DOMAIN
from custom_components.doorman.notifications import async_setup_notifications


@pytest.fixture
def mock_store():
    store = MagicMock()
    store.get_notification_targets = MagicMock(return_value=["notify.mobile_app"])
    return store


async def test_notification_dispatched_on_user_authenticated(hass: HomeAssistant, mock_store):
    """UserAuthenticated event fires a notify service call for each target."""
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
            "params": {"user": {"id": "uuid-abc", "name": "Jane"}},
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["message"] == "Jane opened the intercom"
    assert calls[0].data["title"] == "Doorman"


async def test_no_notification_for_non_authenticated_events(hass: HomeAssistant, mock_store):
    """Events other than UserAuthenticated do not trigger notifications."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    mock_store.get_notification_targets.return_value = ["notify.mobile_app"]

    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "CardEntered", "params": {"user": {"id": "uuid-abc", "name": "Jane"}}},
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
        {"event_type": "UserAuthenticated", "params": {"user": {"id": "uuid-abc", "name": "Jane"}}},
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
        {"event_type": "UserAuthenticated", "params": {"user": {"id": "uuid-abc"}}},
    )
    await hass.async_block_till_done()

    assert calls[0].data["message"] == "Someone opened the intercom"


async def test_no_notification_when_store_missing(hass: HomeAssistant):
    """Gracefully skip when the store is not yet initialised."""
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "UserAuthenticated", "params": {"user": {"id": "uuid-abc", "name": "Jane"}}},
    )
    await hass.async_block_till_done()

    assert len(calls) == 0
