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

PLATFORMS = ["sensor", "switch", "event"]
