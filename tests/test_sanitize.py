"""Tests for credential / access-log redaction helpers."""
from __future__ import annotations

from custom_components.doorman.sanitize import (
    sanitize_directory_user,
    sanitize_log_event,
)


def test_sanitize_directory_user_redacts_secrets() -> None:
    """pin/card/code values become presence flags; other fields pass through."""
    user = {
        "uuid": "u1",
        "name": "Jane",
        "enabled": True,
        "pin": "1234",
        "card": ["AABB", "CCDD"],
        "code": ["99"],
        "validFrom": 1,
        "validTo": 2,
    }
    out = sanitize_directory_user(user)
    assert "pin" not in out
    assert "card" not in out
    assert "code" not in out
    assert out["has_pin"] is True
    assert out["has_card"] is True
    assert out["card_count"] == 2
    assert out["has_code"] is True
    assert out["code_count"] == 1
    assert out["uuid"] == "u1"
    assert out["name"] == "Jane"
    assert out["enabled"] is True
    assert out["validFrom"] == 1
    assert out["validTo"] == 2


def test_sanitize_directory_user_empty_credentials() -> None:
    """Empty credentials produce False / zero counts."""
    out = sanitize_directory_user(
        {"uuid": "u1", "name": "X", "pin": "", "card": [], "code": [""]}
    )
    assert out["has_pin"] is False
    assert out["has_card"] is False
    assert out["card_count"] == 0
    assert out["has_code"] is False
    assert out["code_count"] == 0


def test_sanitize_log_event_strips_keypressed_digits() -> None:
    """KeyPressed PIN keystrokes are dropped; %N quick-dial keys stay."""
    digit = sanitize_log_event(
        {"id": 1, "event": "KeyPressed", "params": {"key": "5"}, "utcTime": 1}
    )
    assert "key" not in digit["params"]

    doorbell = sanitize_log_event(
        {"id": 2, "event": "KeyPressed", "params": {"key": "%1"}, "utcTime": 1}
    )
    assert doorbell["params"]["key"] == "%1"

    numeric = sanitize_log_event(
        {"id": 3, "event": "KeyPressed", "params": {"digit": 7}, "utcTime": 1}
    )
    assert "digit" not in numeric["params"]


def test_sanitize_log_event_strips_pin_and_code_params() -> None:
    """params.pin / params.code are stripped on any event type."""
    event = sanitize_log_event(
        {
            "id": 1,
            "event": "CodeEntered",
            "params": {"code": "1234", "uuid": "u1", "valid": True},
            "utcTime": 1,
        }
    )
    assert "code" not in event["params"]
    assert event["params"]["uuid"] == "u1"
    assert event["params"]["valid"] is True

    with_pin = sanitize_log_event(
        {"id": 2, "event": "UserAuthenticated", "params": {"pin": "9999", "name": "A"}}
    )
    assert "pin" not in with_pin["params"]
    assert with_pin["params"]["name"] == "A"


def test_sanitize_log_event_does_not_mutate_input() -> None:
    """Sanitization returns a copy; the original event is untouched."""
    original = {"id": 1, "event": "KeyPressed", "params": {"key": "5"}}
    sanitize_log_event(original)
    assert original["params"]["key"] == "5"
