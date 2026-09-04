"""Config flow for Doorman."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api_client import DoormanAuthError, DoormanConnectionError, TwoNApiClient
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_POLL_INTERVAL,
    CONF_USE_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DEFAULT_POLL_INTERVAL,
    DEFAULT_USE_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
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


class DoormanConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the Doorman setup flow."""

    VERSION = 1

    @staticmethod
    @config_entries.callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> DoormanOptionsFlow:
        return DoormanOptionsFlow()

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
                return self.async_create_entry(
                    title=info.get("deviceName") or user_input[CONF_HOST],
                    data=user_input,
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: dict  # noqa: ARG002
    ) -> config_entries.ConfigFlowResult:
        """Initiate re-authentication when credentials are rejected."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            client = TwoNApiClient(
                session,
                entry.data[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                entry.data.get(CONF_USE_SSL, DEFAULT_USE_SSL),
                entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
            )
            try:
                await client.get_system_info()
            except DoormanAuthError:
                errors["base"] = "invalid_auth"
            except DoormanConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                # Read before the update: HA runs update listeners eagerly, so
                # by the time async_update_entry returns the reload it
                # triggered has already moved the entry to UNLOAD_IN_PROGRESS.
                was_loaded = entry.state is config_entries.ConfigEntryState.LOADED
                changed = self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, **user_input},
                )
                # async_update_entry fires the entry's update listeners on *any*
                # change, not just an options change, and __init__.py registers
                # one that reloads the entry — so a changed, loaded entry is
                # already being reloaded and calling async_reload here too gave
                # one reauth two full teardown/rebuild cycles (two rounds of
                # device calls, two backfill subscriptions, two windows with no
                # doorman.* services).
                #
                # Reload explicitly only when that listener cannot have run:
                # nothing changed (the user re-entered the same credentials), or
                # the entry is not loaded — the usual case, since credentials
                # rejected at startup raise ConfigEntryAuthFailed before the
                # listener is registered. Dropping this call outright would then
                # leave the entry sitting in SETUP_ERROR after a successful
                # reauth.
                #
                # config_entries.OptionsFlowWithReload would express this
                # natively but is not used here — the same-credentials /
                # SETUP_ERROR cases need an explicit reload decision.
                if not (changed and was_loaded):
                    await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
            description_placeholders={"host": entry.data[CONF_HOST]},
        )
    async def async_step_reconfigure(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        """Change connection details (host/credentials/SSL) without re-adding."""
        entry = self._get_reconfigure_entry()
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
                # Guard against accidentally pointing the entry at a different
                # intercom — stored user links, notification targets, and log
                # history are all keyed to the original device's UUIDs.
                serial = info.get("serialNumber") or user_input[CONF_HOST]
                if serial != entry.unique_id:
                    return self.async_abort(reason="different_device")
                return self.async_update_reload_and_abort(entry, data=user_input)

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(STEP_SCHEMA, entry.data),
            errors=errors,
            description_placeholders={"device": entry.title},
        )


OPTIONS_SCHEMA = vol.Schema(
    {
        vol.Optional(
            CONF_POLL_INTERVAL,
            default=DEFAULT_POLL_INTERVAL,
        ): vol.All(int, vol.Range(min=10, max=3600)),
    }
)


class DoormanOptionsFlow(config_entries.OptionsFlow):
    """Allow changing integration options (e.g. poll interval) post-setup."""

    async def async_step_init(
        self, user_input: dict | None = None
    ) -> config_entries.ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current_interval = self.config_entry.options.get(
            CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        CONF_POLL_INTERVAL,
                        default=current_interval,
                    ): vol.All(int, vol.Range(min=10, max=3600)),
                }
            ),
        )
