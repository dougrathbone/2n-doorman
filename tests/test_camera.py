"""Tests for the Doorman camera platform."""
from __future__ import annotations

import pytest
from homeassistant.components.camera import async_get_image
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import MOCK_JPEG


@pytest.mark.asyncio
async def test_camera_entity_created_when_device_has_camera(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """A camera entity is registered when camera/caps reports resolutions."""
    state = hass.states.get("camera.doorman_camera")
    assert state is not None
    mock_2n_client.get_camera_caps.assert_called()


@pytest.mark.asyncio
async def test_camera_no_entity_without_camera(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Camera-less devices (e.g. keypad-only Access Units) get no entity."""
    mock_2n_client.get_camera_caps.return_value = {}
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("camera.doorman_camera") is None


@pytest.mark.asyncio
async def test_camera_returns_jpeg_snapshot(
    hass: HomeAssistant,
    setup_doorman: MockConfigEntry,
    mock_2n_client,
) -> None:
    """async_get_image returns the snapshot bytes from the device."""
    image = await async_get_image(hass, "camera.doorman_camera")
    assert image.content == MOCK_JPEG
    mock_2n_client.get_camera_snapshot.assert_called_with(640, 480)


@pytest.mark.asyncio
async def test_camera_snapshot_prefers_supported_resolution(
    hass: HomeAssistant,
    doorman_config_entry: MockConfigEntry,
    mock_2n_client,
) -> None:
    """Falls back to the largest advertised resolution when 640x480 is absent."""
    mock_2n_client.get_camera_caps.return_value = {
        "jpegResolution": [{"width": 320, "height": 240}, {"width": 176, "height": 144}]
    }
    doorman_config_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(doorman_config_entry.entry_id)
    await hass.async_block_till_done()

    await async_get_image(hass, "camera.doorman_camera")
    mock_2n_client.get_camera_snapshot.assert_called_with(320, 240)
