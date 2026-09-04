"""Tests for Doorman entity-ID / DeviceInfo helpers."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.doorman.const import CONF_HOST, CONF_USE_SSL, DOMAIN
from custom_components.doorman.helpers import (
    build_device_info,
    device_slug,
    pinned_entity_id,
)
from tests.conftest import MOCK_DEVICE_INFO, MOCK_DEVICE_SLUG, doorman_eid


def _coord(serial: str | None = "10-12345678", **extra) -> MagicMock:
    info = {**MOCK_DEVICE_INFO, **extra}
    if serial is None:
        info.pop("serialNumber", None)
    else:
        info["serialNumber"] = serial
    coord = MagicMock()
    coord.device_info = info
    return coord


def test_device_slug_from_serial() -> None:
    entry = SimpleNamespace(entry_id="abcdef12-3456-7890-abcd-ef1234567890")
    assert device_slug(_coord("10-12345678"), entry) == "1012345678"


def test_device_slug_falls_back_to_entry_id() -> None:
    entry = SimpleNamespace(entry_id="AbCdEf12-3456-7890-abcd-ef1234567890")
    assert device_slug(_coord(""), entry) == "abcdef12"
    assert device_slug(_coord("ab"), entry) == "abcdef12"
    assert device_slug(_coord(None), entry) == "abcdef12"


def test_pinned_entity_id_format() -> None:
    entry = SimpleNamespace(entry_id="ignored-when-serial-long")
    assert (
        pinned_entity_id("switch", "relay_1", _coord(), entry)
        == f"switch.doorman_{MOCK_DEVICE_SLUG}_relay_1"
    )


def test_build_device_info_http() -> None:
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Front Door",
        data={CONF_HOST: "192.168.1.100", CONF_USE_SSL: False},
    )
    info = build_device_info(_coord(), entry)
    assert info["identifiers"] == {(DOMAIN, "entry-1")}
    assert info["name"] == "Front Door"
    assert info["manufacturer"] == "2N"
    assert info["model"] == "535v1"
    assert info["hw_version"] == "535v1"
    assert info["sw_version"] == "2.49.0.38"
    assert info["serial_number"] == "10-12345678"
    assert info["configuration_url"] == "http://192.168.1.100/"


def test_build_device_info_defaults_to_https() -> None:
    """Omitting use_ssl uses DEFAULT_USE_SSL (True)."""
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Front Door",
        data={CONF_HOST: "192.168.1.100"},
    )
    info = build_device_info(_coord(), entry)
    assert info["configuration_url"] == "https://192.168.1.100/"


def test_build_device_info_https_and_model_key() -> None:
    entry = SimpleNamespace(
        entry_id="entry-1",
        title="Gate",
        data={CONF_HOST: "intercom.local", CONF_USE_SSL: True},
    )
    info = build_device_info(_coord(model="IP Verso"), entry)
    assert info["model"] == "IP Verso"
    assert info["configuration_url"] == "https://intercom.local/"


@pytest.mark.asyncio
async def test_relay_name_and_entity_id_are_device_scoped(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Relay keeps a fixed friendly name, has_entity_name False, device-scoped ID."""
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    entity_id = doorman_eid("switch", "relay_1")
    state = hass.states.get(entity_id)
    assert state is not None
    assert state.name == "Doorman Relay 1"
    assert MOCK_DEVICE_SLUG in entity_id

    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    entity = registry.async_get(entity_id)
    assert entity is not None
    assert entity.has_entity_name is False


@pytest.mark.asyncio
async def test_entity_ids_do_not_collide_across_entries(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Two config entries with distinct serials get distinct entity IDs."""
    from tests.conftest import setup_two_entries

    # MockConfigEntry uses time-based ULIDs that often share an 8-char prefix,
    # so exercise the serial-based path (the normal production case).
    mock_2n_client.get_system_info.side_effect = [
        {**MOCK_DEVICE_INFO, "serialNumber": "10-AAAA1111"},
        {**MOCK_DEVICE_INFO, "serialNumber": "10-BBBB2222"},
    ]
    entry1, entry2 = await setup_two_entries(hass, doorman_config_entry)

    coord1 = hass.data[DOMAIN][entry1.entry_id]
    coord2 = hass.data[DOMAIN][entry2.entry_id]
    id1 = pinned_entity_id("switch", "relay_1", coord1, entry1)
    id2 = pinned_entity_id("switch", "relay_1", coord2, entry2)
    assert id1 == "switch.doorman_10aaaa1111_relay_1"
    assert id2 == "switch.doorman_10bbbb2222_relay_1"
    assert id1 != id2
    assert hass.states.get(id1) is not None
    assert hass.states.get(id2) is not None


@pytest.mark.asyncio
async def test_entity_id_falls_back_to_entry_id_slug(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Missing serial uses a lowercase entry_id-derived slug."""
    mock_2n_client.get_system_info.return_value = {
        **MOCK_DEVICE_INFO,
        "serialNumber": "",
    }
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    coordinator = hass.data[DOMAIN][doorman_config_entry.entry_id]
    expected = pinned_entity_id(
        "switch", "relay_1", coordinator, doorman_config_entry
    )
    slug = doorman_config_entry.entry_id.replace("-", "").lower()[:8]
    assert expected == f"switch.doorman_{slug}_relay_1"
    assert expected == expected.lower()
    assert hass.states.get(expected) is not None
