"""Constants for Doorman."""

DOMAIN = "doorman"

CONF_HOST = "host"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_USE_SSL = "use_ssl"
CONF_VERIFY_SSL = "verify_ssl"
CONF_POLL_INTERVAL = "poll_interval"

DEFAULT_POLL_INTERVAL = 30
DEFAULT_USE_SSL = False
DEFAULT_VERIFY_SSL = True

PANEL_URL = f"/api/{DOMAIN}"
PANEL_TITLE = "Doorman"
PANEL_ICON = "mdi:door-closed-lock"

STORAGE_KEY = f"{DOMAIN}.storage"
STORAGE_VERSION = 2

PLATFORMS = ["sensor", "switch", "event"]

# ── Sync roles ──────────────────────────────────────────────────────────────
CONF_SYNC_ROLE = "sync_role"
CONF_SYNC_TARGET = "sync_target"

SYNC_ROLE_NONE = "none"
SYNC_ROLE_LEADER = "leader"
SYNC_ROLE_FOLLOWER = "follower"
