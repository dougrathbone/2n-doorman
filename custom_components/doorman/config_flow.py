"""Config flow for Doorman."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import DoormanAuthError, DoormanConnectionError, TwoNApiClient
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_SYNC_ROLE,
    CONF_SYNC_TARGET,
    CONF_USE_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    SYNC_ROLE_FOLLOWER,
    SYNC_ROLE_LEADER,
    SYNC_ROLE_NONE,
)

STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_USE_SSL, default=DEFAULT_USE_SSL): bool,
        vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    }
)

SYNC_ROLE_SCHEMA = vol.Schema({
    vol.Optional(CONF_SYNC_ROLE, default=SYNC_ROLE_NONE): vol.In({
        SYNC_ROLE_NONE: "None",
        SYNC_ROLE_LEADER: "Leader",
        SYNC_ROLE_FOLLOWER: "Follower",
    }),
})


def _get_other_entries(
    hass, exclude_entry_id: str | None = None
) -> dict[str, str]:
    """Return {entry_id: display_name} for all other doorman config entries."""
    other: dict[str, str] = {}

    # Prefer coordinator device_info if available (richer names)
    coordinators = hass.data.get(DOMAIN, {})
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.entry_id == exclude_entry_id:
            continue
        coord = coordinators.get(entry.entry_id)
        if coord and coord.device_info:
            name = coord.device_info.get("deviceName", entry.title)
            serial = coord.device_info.get("serialNumber", "")
            other[entry.entry_id] = f"{name} ({serial})" if serial else name
        else:
            other[entry.entry_id] = entry.title
    return other


class DoormanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Doorman setup flow.

    Steps:
      1. user         — credentials + SSL
      2. sync_role    — none / leader / follower (only if other entries exist)
      3. pick_leader  — select leader device (only if role is follower)
    """

    VERSION = 1

    def __init__(self) -> None:
        self._user_input: dict[str, Any] = {}
        self._title: str = ""
        self._role: str = SYNC_ROLE_NONE

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> DoormanOptionsFlowHandler:
        return DoormanOptionsFlowHandler(config_entry)

    async def async_step_user(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = TwoNApiClient(
                session,
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input.get(CONF_USE_SSL, DEFAULT_USE_SSL),
                user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            try:
                info = await client.get_system_info()
            except DoormanAuthError:
                errors["base"] = "invalid_auth"
            except DoormanConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                unique_id = info.get("serialNumber") or user_input[CONF_HOST]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                self._user_input = user_input
                self._title = info.get("deviceName") or user_input[CONF_HOST]

                # If other doorman entries exist, offer sync role selection
                if _get_other_entries(self.hass):
                    return await self.async_step_sync_role()

                return self._create_entry()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )

    async def async_step_sync_role(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._role = user_input.get(CONF_SYNC_ROLE, SYNC_ROLE_NONE)
            if self._role == SYNC_ROLE_FOLLOWER:
                return await self.async_step_pick_leader()
            return self._create_entry()

        return self.async_show_form(
            step_id="sync_role",
            data_schema=SYNC_ROLE_SCHEMA,
        )

    async def async_step_pick_leader(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        other_entries = _get_other_entries(self.hass)

        if not other_entries:
            return self.async_show_form(
                step_id="pick_leader",
                data_schema=vol.Schema({}),
                errors={"base": "no_other_devices"},
            )

        if user_input is not None:
            target = user_input.get(CONF_SYNC_TARGET)
            if target in other_entries:
                return self._create_entry(sync_target=target)
            return self.async_show_form(
                step_id="pick_leader",
                data_schema=vol.Schema({
                    vol.Required(CONF_SYNC_TARGET): vol.In(other_entries),
                }),
                errors={"base": "invalid_target"},
            )

        return self.async_show_form(
            step_id="pick_leader",
            data_schema=vol.Schema({
                vol.Required(CONF_SYNC_TARGET): vol.In(other_entries),
            }),
        )

    def _create_entry(self, sync_target: str | None = None) -> config_entries.ConfigFlowResult:
        options: dict[str, Any] = {}
        if self._role != SYNC_ROLE_NONE:
            options[CONF_SYNC_ROLE] = self._role
            if sync_target:
                options[CONF_SYNC_TARGET] = sync_target
        return self.async_create_entry(
            title=self._title,
            data=self._user_input,
            options=options,
        )


class DoormanOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Doorman options (sync role configuration).

    Two-step flow:
      Step 1 (init)        — pick sync role (none / leader / follower)
      Step 2 (pick_leader) — only shown when role is follower; pick leader device
    """

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._entry = config_entry
        self._role: str = SYNC_ROLE_NONE

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            self._role = user_input.get(CONF_SYNC_ROLE, SYNC_ROLE_NONE)
            if self._role == SYNC_ROLE_FOLLOWER:
                return await self.async_step_pick_leader()
            return self.async_create_entry(
                data={CONF_SYNC_ROLE: self._role, CONF_SYNC_TARGET: None}
            )

        current_role = self._entry.options.get(CONF_SYNC_ROLE, SYNC_ROLE_NONE)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Optional(CONF_SYNC_ROLE, default=current_role): vol.In({
                    SYNC_ROLE_NONE: "None",
                    SYNC_ROLE_LEADER: "Leader",
                    SYNC_ROLE_FOLLOWER: "Follower",
                }),
            }),
        )

    async def async_step_pick_leader(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        other_entries = _get_other_entries(self.hass, self._entry.entry_id)

        if not other_entries:
            return self.async_show_form(
                step_id="pick_leader",
                data_schema=vol.Schema({}),
                errors={"base": "no_other_devices"},
            )

        if user_input is not None:
            target = user_input.get(CONF_SYNC_TARGET)
            if target not in other_entries:
                return self.async_show_form(
                    step_id="pick_leader",
                    data_schema=vol.Schema({
                        vol.Required(CONF_SYNC_TARGET): vol.In(other_entries),
                    }),
                    errors={"base": "invalid_target"},
                )
            return self.async_create_entry(
                data={CONF_SYNC_ROLE: SYNC_ROLE_FOLLOWER, CONF_SYNC_TARGET: target}
            )

        current_target = self._entry.options.get(CONF_SYNC_TARGET, "")
        return self.async_show_form(
            step_id="pick_leader",
            data_schema=vol.Schema({
                vol.Required(CONF_SYNC_TARGET, default=current_target): vol.In(
                    other_entries
                ),
            }),
        )
