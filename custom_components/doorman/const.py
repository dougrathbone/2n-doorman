"""Constants for Doorman."""

DOMAIN = "doorman"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"
CONF_POLL_INTERVAL = "poll_interval"
CONF_DOORBELL_KEY_CODE = "doorbell_key_code"
CONF_DOORBELL_TARGETS = "doorbell_targets"

DEFAULT_POLL_INTERVAL = 30
# 2N Verso's factory "quick dial 1" button emits key='%1' in KeyPressed events.
# Other models (Force, IP Style) share the same convention.
DEFAULT_DOORBELL_KEY_CODE = "%1"
DEFAULT_USE_SSL = False
DEFAULT_VERIFY_SSL = True

PANEL_URL = f"/api/{DOMAIN}"
PANEL_TITLE = "Doorman"
PANEL_ICON = "mdi:door-closed-lock"

STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 1

PLATFORMS = ["sensor", "switch", "event"]
