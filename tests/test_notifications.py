"""Tests for Doorman push notification dispatch."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import (
    CONF_ACCESS_CHANNEL_ANDROID,
    CONF_ACCESS_SOUND_IOS,
    CONF_DOORBELL_CHANNEL_ANDROID,
    CONF_DOORBELL_SOUND_IOS,
    CONF_DOORBELL_TARGETS,
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    DOMAIN,
)
from custom_components.doorman.notifications import async_setup_notifications
from custom_components.doorman.storage import DoormanStore


@pytest.fixture
def mock_store(hass: HomeAssistant) -> DoormanStore:
    """A real DoormanStore with the disk layer stubbed out.

    Deliberately not a bare MagicMock: the dispatcher reads settings values
    straight into the notify payload, so a Mock would be silently truthy and
    land a ``<MagicMock …>`` in ``data.push.sound``. Using the real object
    also exercises the defaults/merge logic the panel relies on.
    """
    store = DoormanStore(hass)
    store._store = MagicMock()
    store._store.async_save = AsyncMock()
    # Every 2N UUID notifies the same target — these tests are about dispatch
    # and presentation, not about per-user target lookup.
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


# ─── Per-flow presentation settings ──────────────────────────────────────────

async def test_access_notification_includes_ios_sound_when_configured(
    hass: HomeAssistant, mock_store
):
    """access_sound_ios flows through as data.push.sound on the access flow."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry = MockConfigEntry(
        domain=DOMAIN, title="North Gate",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_ACCESS_SOUND_IOS: "US-EN-Alexa-Front-Door-Opened.wav"}
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

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

    assert calls[0].data["data"]["push"] == {"sound": "US-EN-Alexa-Front-Door-Opened.wav"}
    assert "channel" not in calls[0].data["data"]


async def test_access_notification_includes_android_channel_when_configured(
    hass: HomeAssistant, mock_store
):
    """access_channel_android flows through as data.channel on the access flow."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry = MockConfigEntry(
        domain=DOMAIN, title="North Gate",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_ACCESS_CHANNEL_ANDROID: "doorman_access"}
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

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

    assert calls[0].data["data"]["channel"] == "doorman_access"


async def test_access_and_doorbell_flows_use_independent_sound_config(
    hass: HomeAssistant, mock_store
):
    """A doorbell press uses doorbell_sound_ios, not access_sound_ios — proving the flows are
    independent (per user feedback: 'ideally I could pick the accustomed sound for doorbell
    and a different sound for other notifications')."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry = MockConfigEntry(
        domain=DOMAIN, title="Front Door",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_ACCESS_SOUND_IOS: "US-EN-Alexa-Front-Door-Opened.wav",
            CONF_DOORBELL_SOUND_IOS: "US-EN-Alexa-Mail-Has-Arrived.wav",
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "DoorbellPressed",
            "params": {"key": "%1"},
        },
    )
    await hass.async_block_till_done()

    assert calls[0].data["data"]["push"] == {"sound": "US-EN-Alexa-Mail-Has-Arrived.wav"}


# ─── Doorbell dispatch ───────────────────────────────────────────────────────

async def test_doorbell_dispatches_to_configured_targets(hass: HomeAssistant, mock_store):
    """DoorbellPressed dispatches to the notify targets stored for the entry."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry = MockConfigEntry(
        domain=DOMAIN, title="Front Door",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_CHANNEL_ANDROID: "doorbell",
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "DoorbellPressed",
            "params": {"key": "%1"},
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["title"] == "Doorbell"
    assert calls[0].data["message"] == "Front Door: someone rang the doorbell"
    assert calls[0].data["data"]["tag"] == f"doorman_doorbell_{entry.entry_id}"
    assert calls[0].data["data"]["channel"] == "doorbell"


async def test_doorbell_sends_nothing_when_no_targets_configured(
    hass: HomeAssistant, mock_store
):
    """Empty doorbell_targets means no dispatch — protects against forgotten stubs."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry = MockConfigEntry(
        domain=DOMAIN, title="Front Door",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    await mock_store.set_notification_settings(entry.entry_id, {CONF_DOORBELL_TARGETS: []})
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "DoorbellPressed",
            "params": {"key": "%1"},
        },
    )
    await hass.async_block_till_done()

    assert calls == []


async def test_doorbell_without_entry_id_is_skipped(hass: HomeAssistant, mock_store):
    """Doorbell targets are stored per entry — no entry_id means no dispatch."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))

    async_setup_notifications(hass)
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {"event_type": "DoorbellPressed", "params": {"key": "%1"}},
    )
    await hass.async_block_till_done()

    assert calls == []


async def test_doorbell_targets_are_isolated_per_entry(hass: HomeAssistant, mock_store):
    """A press on device A must not ring device B's phones.

    The store is a single shared instance across every config entry, so its
    notification settings have to be keyed by entry_id. If they were flat,
    both doors would share one target list.
    """
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry_a = MockConfigEntry(
        domain=DOMAIN, title="Front Door",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry_b = MockConfigEntry(
        domain=DOMAIN, title="Back Gate",
        data={CONF_HOST: "192.168.1.200", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry_a.add_to_hass(hass)
    entry_b.add_to_hass(hass)
    await mock_store.set_notification_settings(
        entry_a.entry_id,
        {CONF_DOORBELL_TARGETS: ["notify.phone_a"], CONF_DOORBELL_SOUND_IOS: "a.wav"},
    )
    await mock_store.set_notification_settings(
        entry_b.entry_id,
        {CONF_DOORBELL_TARGETS: ["notify.phone_b"], CONF_DOORBELL_SOUND_IOS: "b.wav"},
    )

    calls_a, calls_b = [], []
    hass.services.async_register("notify", "phone_a", lambda call: calls_a.append(call))
    hass.services.async_register("notify", "phone_b", lambda call: calls_b.append(call))

    async_setup_notifications(hass)
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry_a.entry_id,
            "event_type": "DoorbellPressed",
            "params": {"key": "%1"},
        },
    )
    await hass.async_block_till_done()

    assert len(calls_a) == 1
    assert calls_a[0].data["message"] == "Front Door: someone rang the doorbell"
    assert calls_a[0].data["data"]["push"] == {"sound": "a.wav"}
    assert calls_b == []
