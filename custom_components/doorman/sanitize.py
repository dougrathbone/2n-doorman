"""Redaction helpers — credentials must not leave the device API unmasked."""
from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.helpers import config_validation as cv

# PIN / access code: 2–15 digits, or "" to clear on update.
PIN_OR_CODE = vol.All(cv.string, vol.Any("", vol.Match(r"^\d{2,15}$")))
# RFID / card number — keep bounded; empty clears on update.
CARD = vol.All(cv.string, vol.Length(max=32))

# Fields that may carry secrets from the 2N directory — never return them
# over WebSocket; replace with presence/count flags instead.
_SECRET_USER_KEYS = frozenset({"pin", "card", "code"})


def sanitize_directory_user(user: dict[str, Any]) -> dict[str, Any]:
    """Return a directory user dict safe to send over WebSocket.

    Drops ``pin`` / ``card`` / ``code`` values and replaces them with
    ``has_pin`` / ``has_card`` / ``card_count`` / ``has_code`` / ``code_count``.
    All other fields (uuid, name, enabled, validity, annotations) pass through.
    """
    pin = user.get("pin") or ""
    cards = [c for c in (user.get("card") or []) if c]
    codes = [c for c in (user.get("code") or []) if c]
    return {
        **{k: v for k, v in user.items() if k not in _SECRET_USER_KEYS},
        "has_pin": bool(pin),
        "has_card": bool(cards),
        "card_count": len(cards),
        "has_code": bool(codes),
        "code_count": len(codes),
    }


def sanitize_log_event(event: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a 2N log event with credential-like params removed.

    * ``params.pin`` / ``params.code`` are always dropped.
    * On ``KeyPressed``, bare ``key`` / ``digit`` values (PIN keystrokes) are
      stripped; quick-dial ``%N`` keys are kept so the doorbell flow and log
      remain useful.
    """
    if not isinstance(event, dict):
        return event
    out = dict(event)
    params = event.get("params")
    if not isinstance(params, dict):
        return out
    params = dict(params)
    params.pop("pin", None)
    params.pop("code", None)
    if event.get("event") == "KeyPressed":
        for field in ("key", "digit"):
            value = params.get(field)
            if isinstance(value, str) and not value.startswith("%"):
                params.pop(field, None)
            elif isinstance(value, (int, float)):
                # Bare keypad digits sometimes arrive as numbers.
                params.pop(field, None)
    out["params"] = params
    return out
