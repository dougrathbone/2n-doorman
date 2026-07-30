"""Constants for Doorman."""

DOMAIN = "doorman"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"
CONF_POLL_INTERVAL = "poll_interval"
CONF_NOTIFICATION_SOUND_IOS = "notification_sound_ios"
CONF_NOTIFICATION_CHANNEL_ANDROID = "notification_channel_android"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_USE_SSL = False
DEFAULT_VERIFY_SSL = True

PANEL_URL = f"/api/{DOMAIN}"
PANEL_TITLE = "Doorman"
PANEL_ICON = "mdi:door-closed-lock"

STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "switch", "event"]
