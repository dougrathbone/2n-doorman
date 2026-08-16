"""Constants for Doorman."""

DOMAIN = "doorman"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"
CONF_POLL_INTERVAL = "poll_interval"

# Per-flow push-notification presentation, configured from the sidebar
# panel (not the options-flow) so we can offer a real dropdown of iOS
# Companion sounds + explanatory prose for the Android notification-channel
# concept.
#
# These are DoormanStore keys, not config-entry option keys: see
# `notification_settings` in storage.py for why they are not in
# entry.options. The names double as the WebSocket wire keys.
CONF_ACCESS_SOUND_IOS = "access_sound_ios"
CONF_ACCESS_CHANNEL_ANDROID = "access_channel_android"
CONF_DOORBELL_SOUND_IOS = "doorbell_sound_ios"
CONF_DOORBELL_CHANNEL_ANDROID = "doorbell_channel_android"

# Doorbell dispatch.
CONF_DOORBELL_KEY_CODE = "doorbell_key_code"
CONF_DOORBELL_TARGETS = "doorbell_targets"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_USE_SSL = False
DEFAULT_VERIFY_SSL = True
# 2N Verso's factory "quick dial 1" button emits key='%1' in KeyPressed
# events. Other models (Force, IP Style) share the same convention.
DEFAULT_DOORBELL_KEY_CODE = "%1"

PANEL_URL = f"/api/{DOMAIN}"
PANEL_TITLE = "Doorman"
PANEL_ICON = "mdi:door-closed-lock"

STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 1

# Access-log history lives in its own per-config-entry store
# (``doorman.access_log.<entry_id>``). It is deliberately NOT part of
# STORAGE_KEY: that file holds small config-ish maps and is rewritten in full
# on every save, so appending log events to it would rewrite user links and
# notification targets on every door open.
LOG_STORAGE_KEY = f"{DOMAIN}.access_log"
LOG_STORAGE_VERSION = 1

# Maximum access-log events kept on disk per config entry. Bounded so the
# JSON file cannot grow without limit; oldest events are trimmed first.
MAX_STORED_LOG_EVENTS = 1000

# Debounce window (seconds) for access-log disk writes. A burst of events from
# one log pull coalesces into a single write.
LOG_SAVE_DELAY = 10

# One-shot startup backfill: ask the device for the history it recorded in the
# last N seconds (7 days). A full ``include=all`` drain is only used as a
# fallback — devices keep up to 10 000 events, delivered 128 per pull.
LOG_BACKFILL_SECONDS = 7 * 24 * 60 * 60
# Pull budget for a single backfill run. The device delivers up to 128 events
# per pull, so 5 pulls caps one backfill at 640 events (it was 10 pulls / 1280).
# Combined with the 10 s per-request timeout in ``fetch_log_history`` this bounds
# a worst-case run at roughly 50 s against an unresponsive device. Backfill runs
# as a background task and no longer delays config-entry setup, but the budget
# stays tight so a sick device is not hammered for minutes. A device holding more
# than 640 events inside the window is therefore only partially backfilled;
# events beyond the budget are not retrievable through this path.
LOG_BACKFILL_MAX_PULLS = 5

PLATFORMS = ["sensor", "switch", "event", "camera", "binary_sensor"]
