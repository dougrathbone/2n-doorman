"""Doorman service actions — admin-gated door / credential / call control."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_register_admin_service

from .api_client import DoormanApiError
from .const import DOMAIN
from .coordinator import DoormanCoordinator
from .sanitize import CARD, PIN_OR_CODE
from .storage import DoormanStore

_LOGGER = logging.getLogger(__name__)


def _resolve_coordinator(hass: HomeAssistant, call: ServiceCall) -> DoormanCoordinator:
    """Return the coordinator for a service call, resolving the optional ``device`` field."""
    entries: dict[str, DoormanCoordinator] = hass.data.get(DOMAIN, {})
    device = call.data.get("device")
    if device:
        if device not in entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_device",
                translation_placeholders={"device": device},
            )
        return entries[device]
    if len(entries) == 0:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_devices_configured",
        )
    if len(entries) == 1:
        return next(iter(entries.values()))
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key="multiple_devices_no_selector",
    )


def async_setup_services(hass: HomeAssistant) -> None:
    """Register Doorman service actions (once per HA run, from async_setup).

    The services stay registered for the lifetime of the HA run, including
    while zero entries are loaded — ``_resolve_coordinator`` then raises a
    clean ``no_devices_configured`` validation error instead of the service
    vanishing from the UI and automations.

    All services use ``async_register_admin_service`` so they match the
    admin-only panel / WebSocket AuthZ: they manage door credentials, open
    doors, and control calls.
    """
    if hass.services.has_service(DOMAIN, "create_user"):
        return

    async def handle_create_user(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        user: dict = {"name": call.data["name"]}
        if "enabled" in call.data:
            user["enabled"] = call.data["enabled"]
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
        try:
            await coordinator.client.create_user(user)
        except DoormanApiError as err:
            # Surface the device message without a raw traceback in the HA log
            raise HomeAssistantError(f"create_user failed on the 2N device: {err}") from err
        await coordinator.async_request_refresh()

    async def handle_update_user(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        user: dict = {"uuid": call.data["uuid"]}
        if "name" in call.data and call.data["name"]:
            user["name"] = call.data["name"]
        # An explicitly empty string clears the PIN, mirroring how an empty
        # card/code below clears those credentials.
        if "pin" in call.data:
            user["pin"] = call.data["pin"]
        if "enabled" in call.data:
            user["enabled"] = call.data["enabled"]
        if "card" in call.data:
            user["card"] = [call.data["card"]] if call.data["card"] else []
        if "code" in call.data:
            user["code"] = [call.data["code"]] if call.data["code"] else []
        # The panel sends 0 to clear a validity restriction (the 2N API
        # represents "no restriction" as validFrom/validTo "0").
        if "valid_from" in call.data:
            valid_from = call.data["valid_from"]
            user["validFrom"] = (
                valid_from if isinstance(valid_from, int) else int(valid_from.timestamp())
            )
        if "valid_to" in call.data:
            valid_to = call.data["valid_to"]
            user["validTo"] = (
                valid_to if isinstance(valid_to, int) else int(valid_to.timestamp())
            )
        try:
            await coordinator.client.update_user(user)
        except DoormanApiError as err:
            raise HomeAssistantError(f"update_user failed on the 2N device: {err}") from err
        await coordinator.async_request_refresh()

    async def handle_delete_user(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        try:
            await coordinator.client.delete_user(call.data["uuid"])
        except DoormanApiError as err:
            raise HomeAssistantError(f"delete_user failed on the 2N device: {err}") from err
        store: DoormanStore | None = hass.data.get(f"{DOMAIN}_store")
        if store:
            await store.unlink_user(call.data["uuid"])
            # Drop stale notification targets — a deleted user can never
            # authenticate again, so dispatching for them is dead weight.
            await store.clear_notification_targets(call.data["uuid"])
        await coordinator.async_request_refresh()

    async def handle_grant_access(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        try:
            await coordinator.client.grant_access(
                access_point_id=call.data.get("access_point_id", 1),
                user_uuid=call.data.get("user_uuid"),
            )
        except DoormanApiError as err:
            raise HomeAssistantError(f"grant_access failed on the 2N device: {err}") from err

    async def handle_hangup_calls(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        try:
            hung_up = await coordinator.client.hangup_all_calls()
        except DoormanApiError as err:
            raise HomeAssistantError(f"hangup_calls failed on the 2N device: {err}") from err
        _LOGGER.info(
            "Hung up %d active call(s) on %s",
            hung_up,
            coordinator.device_info.get("deviceName") or "the 2N device",
        )

    async def handle_answer_call(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        try:
            answered = await coordinator.client.answer_ringing_call()
        except DoormanApiError as err:
            raise HomeAssistantError(f"answer_call failed on the 2N device: {err}") from err
        _LOGGER.info(
            "Answered the ringing call on %s"
            if answered
            else "No ringing incoming call on %s to answer",
            coordinator.device_info.get("deviceName") or "the 2N device",
        )

    async def handle_dial(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        number = call.data.get("number")
        users = call.data.get("user_uuids")
        user_list = [u.strip() for u in users.split(",") if u.strip()] if users else None
        try:
            session = await coordinator.client.dial(number=number, users=user_list)
        except DoormanApiError as err:
            raise HomeAssistantError(f"dial failed on the 2N device: {err}") from err
        _LOGGER.info(
            "Started call session %d on %s",
            session,
            coordinator.device_info.get("deviceName") or "the 2N device",
        )

    async def handle_resync_log_history(call: ServiceCall) -> None:
        coordinator = _resolve_coordinator(hass, call)
        device_name = coordinator.device_info.get("deviceName") or "the 2N device"
        # Re-runs the same code path as the startup backfill (and joins it if it
        # is still running), so it inherits the no-notify guarantee: no
        # doorman_access events, no last_access updates.
        fetched, added = await coordinator.async_resync_access_log()
        if fetched == 0:
            # Nothing came back at all. Either the device genuinely has no log,
            # or — far more likely — this firmware does not accept the
            # include=-<seconds> / include=all parameter on /api/log/subscribe.
            # That assumption is unverified against real hardware, so say so.
            _LOGGER.info(
                "Doorman: resync of %s returned no log history at all (0 events "
                "fetched, 0 added). The device did not serve any history — its "
                "firmware may not support the 'include' parameter on "
                "/api/log/subscribe. Enable debug logging for "
                "custom_components.doorman.api_client to see the device's reply.",
                device_name,
            )
        elif added == 0:
            _LOGGER.info(
                "Doorman: resync of %s fetched %d historical event(s), all of which "
                "were already stored — 0 added. History retrieval is working; there "
                "was simply nothing new.",
                device_name,
                fetched,
            )
        else:
            _LOGGER.info(
                "Doorman: resync of %s fetched %d historical event(s) and added %d new "
                "one(s) to the access log.",
                device_name,
                fetched,
                added,
            )

    async_register_admin_service(
        hass,
        DOMAIN,
        "create_user",
        handle_create_user,
        schema=vol.Schema(
            {
                vol.Required("name"): cv.string,
                vol.Optional("enabled"): cv.boolean,
                vol.Optional("pin"): PIN_OR_CODE,
                vol.Optional("card"): CARD,
                vol.Optional("code"): PIN_OR_CODE,
                vol.Optional("valid_from"): cv.datetime,
                vol.Optional("valid_to"): cv.datetime,
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "update_user",
        handle_update_user,
        schema=vol.Schema(
            {
                vol.Required("uuid"): cv.string,
                vol.Optional("name"): cv.string,
                vol.Optional("enabled"): cv.boolean,
                vol.Optional("pin"): PIN_OR_CODE,
                vol.Optional("card"): CARD,
                vol.Optional("code"): PIN_OR_CODE,
                # A datetime sets the restriction; 0 clears it.
                vol.Optional("valid_from"): vol.Any(cv.datetime, 0),
                vol.Optional("valid_to"): vol.Any(cv.datetime, 0),
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "delete_user",
        handle_delete_user,
        schema=vol.Schema(
            {
                vol.Required("uuid"): cv.string,
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "grant_access",
        handle_grant_access,
        schema=vol.Schema(
            {
                vol.Optional("access_point_id", default=1): vol.All(int, vol.Range(min=1)),
                vol.Optional("user_uuid"): cv.string,
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "hangup_calls",
        handle_hangup_calls,
        schema=vol.Schema(
            {
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "answer_call",
        handle_answer_call,
        schema=vol.Schema(
            {
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "dial",
        handle_dial,
        schema=vol.Schema(
            {
                vol.Exclusive("number", "dial_target"): cv.string,
                vol.Exclusive("user_uuids", "dial_target"): cv.string,
                vol.Optional("device"): cv.string,
            }
        ),
    )
    async_register_admin_service(
        hass,
        DOMAIN,
        "resync_log_history",
        handle_resync_log_history,
        schema=vol.Schema(
            {
                vol.Optional("device"): cv.string,
            }
        ),
    )
