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
LOG_BACKFILL_MAX_PULLS = 10

PLATFORMS = ["sensor", "switch", "event"]
