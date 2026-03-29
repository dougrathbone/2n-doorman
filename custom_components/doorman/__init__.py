"""Doorman — 2N intercom access control for Home Assistant."""
from __future__ import annotations

import logging
from pathlib import Path

import voluptuous as vol
from homeassistant.components import panel_custom
from homeassistant.components.frontend import async_remove_panel
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import TwoNApiClient
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
from .storage import DoormanStore
from .websocket import async_setup_websocket

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: ARG001
    hass.data.setdefault(DOMAIN, {})
    return True


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
    await coordinator.async_init_device_info()
    await coordinator.async_config_entry_first_refresh()

    store = DoormanStore(hass)
    await store.async_load()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    hass.data[f"{DOMAIN}_store"] = store

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async_setup_websocket(hass)
    async_setup_notifications(hass)
    _register_services(hass, coordinator)

    # Serve frontend assets and register the sidebar panel
    frontend_dir = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths(
        [StaticPathConfig(PANEL_URL, str(frontend_dir), cache_headers=False)]
    )
    await panel_custom.async_register_panel(
        hass,
        webcomponent_name="doorman-panel",
        frontend_url_path=DOMAIN,
        sidebar_title=PANEL_TITLE,
        sidebar_icon=PANEL_ICON,
        module_url=f"{PANEL_URL}/panel.js",
        embed_iframe=False,
        require_admin=False,
    )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data[DOMAIN]:
            async_remove_panel(hass, DOMAIN)
    return unloaded


# ------------------------------------------------------------------ #
# Services                                                            #
# ------------------------------------------------------------------ #

def _register_services(hass: HomeAssistant, coordinator: DoormanCoordinator) -> None:
    """Register Doorman service actions."""

    async def handle_create_user(call: ServiceCall) -> None:
        user: dict = {"name": call.data["name"]}
        if pin := call.data.get("pin"):
            user["pin"] = pin
        if card := call.data.get("card"):
            user["card"] = [card]
        if code := call.data.get("code"):
            user["code"] = [code]
        if valid_from := call.data.get("valid_from"):
            user["validFrom"] = int(valid_from.timestamp())
        if valid_to := call.data.get("valid_to"):
            user["validTo"] = int(valid_to.timestamp())
        await coordinator.client.create_user(user)
        await coordinator.async_request_refresh()

    async def handle_update_user(call: ServiceCall) -> None:
        user: dict = {"uuid": call.data["uuid"]}
        for field in ("name", "pin"):
            if field in call.data:
                user[field] = call.data[field]
        if "card" in call.data:
            user["card"] = [call.data["card"]] if call.data["card"] else []
        if "code" in call.data:
            user["code"] = [call.data["code"]] if call.data["code"] else []
        await coordinator.client.update_user(user)
        await coordinator.async_request_refresh()

    async def handle_delete_user(call: ServiceCall) -> None:
        await coordinator.client.delete_user(call.data["uuid"])
        # Also remove any stored HA user link
        store: DoormanStore | None = hass.data.get(f"{DOMAIN}_store")
        if store:
            await store.unlink_user(call.data["uuid"])
        await coordinator.async_request_refresh()

    async def handle_grant_access(call: ServiceCall) -> None:
        await coordinator.client.grant_access(
            access_point_id=call.data.get("access_point_id", 1)
        )

    hass.services.async_register(
        DOMAIN,
        "create_user",
        handle_create_user,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Optional("pin"): cv.string,
                vol.Optional("card"): cv.string,
                vol.Optional("code"): cv.string,
                vol.Optional("valid_from"): cv.datetime,
                vol.Optional("valid_to"): cv.datetime,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "update_user",
        handle_update_user,
        schema=vol.Schema(
            {
                vol.Required("uuid"): cv.string,
                vol.Optional("name"): cv.string,
                vol.Optional("pin"): cv.string,
                vol.Optional("card"): cv.string,
                vol.Optional("code"): cv.string,
            }
        ),
    )
    hass.services.async_register(
        DOMAIN,
        "delete_user",
        handle_delete_user,
        schema=vol.Schema({vol.Required("uuid"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN,
        "grant_access",
        handle_grant_access,
        schema=vol.Schema(
            {vol.Optional("access_point_id", default=1): vol.All(int, vol.Range(min=1))}
        ),
    )
