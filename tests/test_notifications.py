"""Tests for Doorman push notification dispatch."""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import (
    CONF_ACCESS_CHANNEL_ANDROID,
    CONF_ACCESS_SOUND_IOS,
    CONF_DOORBELL_ANSWER_ACTION,
    CONF_DOORBELL_ATTACH_CAMERA,
    CONF_DOORBELL_CHANNEL_ANDROID,
    CONF_DOORBELL_NOTIFY_ON_CALL_RINGING,
    CONF_DOORBELL_SOUND_IOS,
    CONF_DOORBELL_TARGETS,
    CONF_DOORBELL_UNLOCK_ACCESS_POINT_ID,
    CONF_DOORBELL_UNLOCK_ACTION,
    CONF_DOORBELL_UNLOCK_USER_UUID,
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


# ─── Companion snapshot + action buttons ─────────────────────────────────────

def _entry_with_coordinator(
    hass: HomeAssistant,
    *,
    serial: str = "12345678",
    call_status_available: bool = True,
):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Front Door",
        data={CONF_HOST: "192.168.1.100", CONF_USERNAME: "u", CONF_PASSWORD: "p"},
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock()
    coordinator.camera_caps = {"jpegResolution": [{"width": 640, "height": 480}]}
    coordinator.device_info = {"serialNumber": serial}
    coordinator.config_entry = entry
    coordinator.call_status_available = call_status_available
    coordinator.client = MagicMock()
    coordinator.client.grant_access = AsyncMock()
    coordinator.client.answer_ringing_call = AsyncMock(return_value=True)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    return entry, coordinator


async def test_doorbell_includes_camera_snapshot_by_default(
    hass: HomeAssistant, mock_store
):
    """Default doorbell notify attaches a JPEG still; Answer is opt-in."""
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_DOORBELL_TARGETS: ["notify.mobile_app"]}
    )
    camera_id = "camera.doorman_12345678_camera"
    hass.states.async_set(camera_id, "idle")

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

    data = calls[0].data["data"]
    assert data["image"] == f"/api/camera_proxy/{camera_id}"
    assert "entity_id" not in data
    assert "actions" not in data


async def test_doorbell_unlock_action_requires_auth_and_ttl(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_ATTACH_CAMERA: False,
            CONF_DOORBELL_UNLOCK_ACTION: True,
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

    actions = calls[0].data["data"]["actions"]
    assert len(actions) == 1
    assert actions[0]["title"] == "Unlock"
    assert actions[0]["authenticationRequired"] is True
    assert actions[0]["destructive"] is True
    assert actions[0]["action"].startswith(f"DOORMAN_UNLOCK|{entry.entry_id}|")
    assert calls[0].data["data"]["timeout"] == 120


async def test_incoming_call_ringing_can_include_answer(hass: HomeAssistant, mock_store):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_ATTACH_CAMERA: False,
            CONF_DOORBELL_NOTIFY_ON_CALL_RINGING: True,
            CONF_DOORBELL_ANSWER_ACTION: True,
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))
    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "CallRinging",
            "params": {"state": "ringing", "direction": "incoming"},
        },
    )
    await hass.async_block_till_done()

    assert calls[0].data["title"] == "Intercom call"
    assert calls[0].data["data"]["actions"][0]["title"] == "Answer"
    assert calls[0].data["data"]["actions"][0]["action"].startswith(
        f"DOORMAN_ANSWER|{entry.entry_id}|"
    )


async def test_outgoing_call_ringing_uses_doorbell_wording_without_answer(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_ATTACH_CAMERA: False,
            CONF_DOORBELL_NOTIFY_ON_CALL_RINGING: True,
            CONF_DOORBELL_ANSWER_ACTION: True,
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))
    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "CallRinging",
            "params": {"state": "ringing", "direction": "outgoing"},
        },
    )
    await hass.async_block_till_done()

    assert calls[0].data["title"] == "Doorbell"
    assert "actions" not in calls[0].data["data"]


async def test_call_ringing_notifies_only_when_enabled(hass: HomeAssistant, mock_store):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_ATTACH_CAMERA: False,
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))
    async_setup_notifications(hass)

    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "CallRinging",
            "params": {"state": "ringing", "direction": "incoming"},
        },
    )
    await hass.async_block_till_done()
    assert calls == []

    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_DOORBELL_NOTIFY_ON_CALL_RINGING: True}
    )
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "CallRinging",
            "params": {"state": "ringing", "direction": "incoming"},
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["title"] == "Intercom call"
    assert calls[0].data["message"] == "Front Door: incoming call"


async def test_doorbell_and_call_ringing_do_not_double_notify(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_ATTACH_CAMERA: False,
            CONF_DOORBELL_NOTIFY_ON_CALL_RINGING: True,
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
    hass.bus.async_fire(
        f"{DOMAIN}_access",
        {
            "entry_id": entry.entry_id,
            "event_type": "CallRinging",
            "params": {"state": "ringing", "direction": "outgoing"},
        },
    )
    await hass.async_block_till_done()

    assert len(calls) == 1
    assert calls[0].data["title"] == "Doorbell"


async def test_companion_unlock_action_grants_access_and_clears(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_UNLOCK_ACTION: True,
            CONF_DOORBELL_UNLOCK_USER_UUID: "uuid-visitor",
            CONF_DOORBELL_UNLOCK_ACCESS_POINT_ID: 2,
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))
    async_setup_notifications(hass)

    issued = int(time.time())
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_UNLOCK|{entry.entry_id}|{issued}"},
    )
    await hass.async_block_till_done()

    coordinator.client.grant_access.assert_awaited_once_with(2, "uuid-visitor")
    assert any(
        c.data.get("message") == "clear_notification"
        and c.data.get("data", {}).get("tag") == f"doorman_doorbell_{entry.entry_id}"
        for c in calls
    )


async def test_companion_unlock_rejects_expired_and_legacy_actions(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_DOORBELL_UNLOCK_ACTION: True}
    )
    async_setup_notifications(hass)

    # Legacy two-segment action (pre-TTL) is rejected.
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_UNLOCK|{entry.entry_id}"},
    )
    await hass.async_block_till_done()
    coordinator.client.grant_access.assert_not_called()

    # Expired issued-at is rejected.
    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_UNLOCK|{entry.entry_id}|{int(time.time()) - 999}"},
    )
    await hass.async_block_till_done()
    coordinator.client.grant_access.assert_not_called()


async def test_companion_answer_action_answers_call(hass: HomeAssistant, mock_store):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_DOORBELL_ANSWER_ACTION: True}
    )
    async_setup_notifications(hass)

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_ANSWER|{entry.entry_id}|{int(time.time())}"},
    )
    await hass.async_block_till_done()

    coordinator.client.answer_ringing_call.assert_awaited_once()


async def test_companion_unlock_ignored_when_disabled(hass: HomeAssistant, mock_store):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, coordinator = _entry_with_coordinator(hass)
    async_setup_notifications(hass)

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_UNLOCK|{entry.entry_id}|{int(time.time())}"},
    )
    await hass.async_block_till_done()

    coordinator.client.grant_access.assert_not_called()


async def test_companion_unlock_skipped_when_coordinator_missing(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_DOORBELL_UNLOCK_ACTION: True}
    )
    hass.data[DOMAIN].pop(entry.entry_id)
    async_setup_notifications(hass)

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_UNLOCK|{entry.entry_id}|{int(time.time())}"},
    )
    await hass.async_block_till_done()

    coordinator.client.grant_access.assert_not_called()


async def test_doorbell_omits_camera_when_entity_missing(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id, {CONF_DOORBELL_TARGETS: ["notify.mobile_app"]}
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

    assert "image" not in calls[0].data["data"]


async def test_doorbell_time_sensitive_adds_priority_flags(
    hass: HomeAssistant, mock_store
):
    from custom_components.doorman.const import CONF_DOORBELL_TIME_SENSITIVE

    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, _coordinator = _entry_with_coordinator(hass)
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_ATTACH_CAMERA: False,
            CONF_DOORBELL_TIME_SENSITIVE: True,
            CONF_DOORBELL_SOUND_IOS: "a.wav",
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

    data = calls[0].data["data"]
    assert data["push"]["sound"] == "a.wav"
    assert data["push"]["interruption-level"] == "time-sensitive"
    assert data["ttl"] == 0
    assert data["priority"] == "high"


async def test_companion_unlock_failure_notifies_targets(
    hass: HomeAssistant, mock_store
):
    hass.data[f"{DOMAIN}_store"] = mock_store
    entry, coordinator = _entry_with_coordinator(hass)
    coordinator.client.grant_access = AsyncMock(side_effect=RuntimeError("denied"))
    await mock_store.set_notification_settings(
        entry.entry_id,
        {
            CONF_DOORBELL_TARGETS: ["notify.mobile_app"],
            CONF_DOORBELL_UNLOCK_ACTION: True,
        },
    )
    calls = []
    hass.services.async_register("notify", "mobile_app", lambda call: calls.append(call))
    async_setup_notifications(hass)

    hass.bus.async_fire(
        "mobile_app_notification_action",
        {"action": f"DOORMAN_UNLOCK|{entry.entry_id}|{int(time.time())}"},
    )
    await hass.async_block_till_done()

    assert any(
        "Unlock failed" in (c.data.get("message") or "")
        and c.data.get("data", {}).get("tag") == f"doorman_doorbell_{entry.entry_id}"
        for c in calls
    )
