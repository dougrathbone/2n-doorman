"""Doorman — 2N intercom access control for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

from homeassistant.components import panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.issue_registry import (
    IssueSeverity,
    async_create_issue,
    async_delete_issue,
)
from homeassistant.loader import async_get_integration

from .api_client import DoormanApiError, DoormanAuthError, TwoNApiClient
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USE_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PANEL_ICON,
    PANEL_TITLE,
    PANEL_URL,
    PLATFORMS,
)
from .coordinator import DoormanCoordinator
from .notifications import async_setup_notifications
from .services import async_setup_services
from .storage import AccessLogStore, DoormanStore
from .websocket import async_setup_websocket

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def _async_reload_on_options_update(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Reload the config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    """Set up everything that belongs to the domain rather than to one device.

    The shared store, the WebSocket commands, the services, the frontend static
    route and the sidebar panel are all registered here — once per HA run,
    before any network I/O, and never torn down.

    They used to be registered at the end of ``async_setup_entry``, after
    ``async_init_device_info()``. That call raises ``ConfigEntryNotReady`` when
    the intercom is unreachable, so an intercom that was unplugged, rebooting
    or on a flapping network at HA startup left the user with no Doorman entry
    in the sidebar at all — precisely when they need the panel to tell them the
    device cannot be reached. (The panel degrades gracefully on its own:
    ``ws_list_devices`` returns an empty list and the panel renders an error
    state.) Registering per entry also meant reloading the only entry removed
    and re-added the panel, flickering the sidebar and briefly dropping every
    ``doorman.*`` service.

    Nothing here is undone by ``async_unload_entry``: the aiohttp static route
    cannot be unregistered anyway, and every WS command and service reports a
    clean "not configured" error when no entry is loaded.
    """
    hass.data.setdefault(DOMAIN, {})

    # The store is shared across all config entries (single STORAGE_KEY) and is
    # read by WS handlers and notifications.py, both of which can run while no
    # entry is loaded — so it is created and loaded here, not per entry.
    if hass.data.get(f"{DOMAIN}_store") is None:
        store = DoormanStore(hass)
        await store.async_load()
        hass.data[f"{DOMAIN}_store"] = store

    async_setup_websocket(hass)
    async_setup_notifications(hass)
    async_setup_services(hass)

    try:
        await _async_register_panel(hass)
    except Exception:  # noqa: BLE001
        # A missing sidebar entry must never be the reason entities, services
        # and events stop working — that is the same failure mode this move
        # exists to fix, just in the other direction.
        _LOGGER.exception(
            "Doorman: could not register the sidebar panel. The integration will "
            "still load — entities, events and services are unaffected — but the "
            "Doorman page will be missing from the sidebar"
        )

    return True


async def _async_register_panel(hass: HomeAssistant) -> None:
    """Serve the frontend assets and add the Doorman sidebar panel."""
    if hass.data.get(f"{DOMAIN}_panel_registered"):
        return

    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_URL, str(frontend_dir), cache_headers=False)]
    )
    # Cache-bust panel.js with the integration version so browsers pick up
    # new frontend code after a HACS update instead of serving a cached copy.
    integration = await async_get_integration(hass, DOMAIN)
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="doorman-panel",
        frontend_url_path=DOMAIN,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{PANEL_URL}/panel.js?v={integration.version}",
        embed_iframe=False,
        # Reaches the panel element as ``panel.config``. panel.js compares the
        # version against its own PANEL_VERSION constant and shows a "reload
        # this page" banner when they differ: after a HACS update the browser
        # re-imports the new module URL into a document that still holds the
        # old custom-element definitions, so the running code is the old code
        # until the tab is reloaded.
        config={"version": integration.version},
        # Admin-only: the panel manages door credentials (PINs, cards,
        # codes) and access linking. The WebSocket commands enforce this
        # independently, but gating the sidebar entry too avoids exposing
        # the tool to non-admin users.
        require_admin=True,
    )
    hass.data[f"{DOMAIN}_panel_registered"] = True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Doorman from a config entry."""
    session = async_get_clientsession(hass)
    client = TwoNApiClient(
        session,
        entry.data[CONF_HOST],
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL),
        entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
    )

    coordinator = DoormanCoordinator(hass, entry, client)
    try:
        await coordinator.async_init_device_info()
    except DoormanAuthError as err:
        # Bad credentials will not fix themselves on retry — tell HA to start
        # the reauth flow. Must be caught before DoormanApiError, which is its
        # parent class.
        raise ConfigEntryAuthFailed(f"Cannot authenticate to 2N device: {err}") from err
    except (DoormanApiError, TimeoutError) as err:
        # Device unreachable or transiently erroring at startup: tell HA to
        # retry the setup later instead of marking the entry permanently failed.
        raise ConfigEntryNotReady(f"Cannot initialise 2N device: {err}") from err

    # Load the durable access log inline: it is a local disk read and the panel
    # needs the persisted history the moment the entry is loaded. Topping it up
    # with the device's on-box history is a network round trip per pull, so it
    # runs as a background task (started further down, once setup cannot fail)
    # rather than holding up the config entry.
    await coordinator.async_load_access_log()

    await coordinator.async_config_entry_first_refresh()

    if not coordinator.has_write_permission:
        device_name = coordinator.device_info.get("deviceName") or entry.data[CONF_HOST]
        async_create_issue(
            hass,
            DOMAIN,
            f"no_write_permission_{entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key="no_write_permission",
            translation_placeholders={
                "device_name": device_name,
                "username": entry.data[CONF_USERNAME],
            },
        )
    else:
        async_delete_issue(hass, DOMAIN, f"no_write_permission_{entry.entry_id}")

    # Created and loaded once per HA run in async_setup — shared by every entry.
    # Seed only this device's users: store.last_access is a flat cross-device map.
    store: DoormanStore | None = hass.data.get(f"{DOMAIN}_store")
    if store is not None:
        known_uuids = {
            str(u["uuid"])
            for u in (coordinator.data or {}).get("users") or []
            if isinstance(u, dict) and u.get("uuid")
        }
        coordinator._last_access = {
            uuid: ts
            for uuid, ts in store.last_access.items()
            if uuid in known_uuids
        }

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # A platform that fails to set up marks the entry SETUP_ERROR, and HA
        # never calls async_unload_entry for an entry that did not finish
        # setting up. Without this the coordinator would stay in hass.data as a
        # phantom device that WS commands and services still resolve to — and
        # would keep hass.data[DOMAIN] non-empty for the rest of the run.
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await coordinator.async_shutdown()
        await coordinator.client.async_close()
        raise

    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options_update))

    # Start the background tasks only after the coordinator is registered in
    # hass.data and all setup steps that can fail have succeeded. Starting them
    # earlier risks leaking a task if a later setup step raises (async_unload_entry
    # only runs for entries that finished setting up).
    coordinator.start_log_listener()
    # Backfill after the listener: the listener's subscription only receives
    # events from the moment it is created, so starting it first means nothing
    # that happens during the backfill can slip through the gap. Anything the
    # two paths both see is deduplicated by the store. Backfill never fires
    # doorman_access events — see DoormanCoordinator.async_backfill_access_log.
    coordinator.start_backfill()

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry.

    Only this entry's own resources are torn down. The panel, static route,
    WebSocket commands, services and shared store belong to the domain (see
    ``async_setup``) and deliberately survive: removing them on the last unload
    made an ordinary reload flicker the sidebar away and briefly delete every
    ``doorman.*`` service, and the static route could not be removed anyway.
    """
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: DoormanCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        # HA calls async_remove_entry *after* unload, so stash this device's
        # user UUIDs for DoormanStore.clear_entry to prune UUID-keyed maps.
        users = (coordinator.data or {}).get("users") or []
        hass.data.setdefault(f"{DOMAIN}_entry_uuids", {})[entry.entry_id] = [
            str(u["uuid"])
            for u in users
            if isinstance(u, dict) and u.get("uuid")
        ]
        await coordinator.async_shutdown()
        await coordinator.client.async_close()
        async_delete_issue(hass, DOMAIN, f"no_write_permission_{entry.entry_id}")
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete per-entry persisted state when the device is removed."""
    await AccessLogStore(hass, entry.entry_id).async_remove()

    store: DoormanStore | None = hass.data.get(f"{DOMAIN}_store")
    if store is None:
        return

    # Prefer UUIDs stashed by async_unload_entry; fall back to a still-loaded
    # coordinator (unload failed / never loaded) so pruning still happens.
    two_n_uuids: list[str] | None = hass.data.get(f"{DOMAIN}_entry_uuids", {}).pop(
        entry.entry_id, None
    )
    if two_n_uuids is None:
        coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if coordinator is not None and coordinator.data:
            two_n_uuids = [
                str(u["uuid"])
                for u in coordinator.data.get("users") or []
                if isinstance(u, dict) and u.get("uuid")
            ]

    await store.clear_entry(entry.entry_id, two_n_uuids)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:  # noqa: ARG001
    """Migrate config entry to the current schema version."""
    _LOGGER.debug("Migrating Doorman entry from version %s", entry.version)
    # VERSION 1 is current — no migrations needed yet.
    return True
