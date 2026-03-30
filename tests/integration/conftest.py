"""Session-scoped fixtures: start services, onboard HA, install Doorman."""
from __future__ import annotations

import asyncio
import os

import pytest

from .helpers import (
    HaClient,
    HaWebSocket,
    Mock2nAdmin,
    install_doorman,
    onboard_ha,
    wait_for_url,
)

# ─── Connection config (override via env in CI) ──────────────────────────────

HA_URL = os.getenv("HA_URL", "http://localhost:8123")
MOCK_2N_URL = os.getenv("MOCK_2N_URL", "http://localhost:8888")
# Host used by HA *inside* Docker to reach the mock server (Docker service name)
MOCK_2N_HOST = os.getenv("MOCK_2N_HOST", "mock-2n")
MOCK_2N_PORT = int(os.getenv("MOCK_2N_PORT", "8888"))

MOCK_2N_FOLLOWER_URL = os.getenv("MOCK_2N_FOLLOWER_URL", "http://localhost:8889")
MOCK_2N_FOLLOWER_HOST = os.getenv("MOCK_2N_FOLLOWER_HOST", "mock-2n-follower")
MOCK_2N_FOLLOWER_PORT = int(os.getenv("MOCK_2N_FOLLOWER_PORT", "8888"))


# ─── Event loop (session scope) ──────────────────────────────────────────────

@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


# ─── Session-scoped HA setup (runs once per test session) ────────────────────

@pytest.fixture(scope="session")
async def ha_token() -> str:
    """Wait for HA to be ready, complete onboarding, return a long-lived token."""
    await wait_for_url(f"{HA_URL}/api/onboarding", timeout=240)
    return await onboard_ha(HA_URL)


@pytest.fixture(scope="session")
async def doorman_installed(ha_token: str) -> dict:
    """Install the Doorman integration once for the whole test session."""
    await wait_for_url(MOCK_2N_URL + "/api/system/info", timeout=30)
    return await install_doorman(HA_URL, ha_token, MOCK_2N_HOST, MOCK_2N_PORT)


@pytest.fixture(scope="session")
async def doorman_follower_installed(ha_token: str, doorman_installed: dict) -> dict:
    """Install a second Doorman integration for the follower device."""
    await wait_for_url(MOCK_2N_FOLLOWER_URL + "/api/system/info", timeout=30)
    return await install_doorman(
        HA_URL, ha_token, MOCK_2N_FOLLOWER_HOST, MOCK_2N_FOLLOWER_PORT
    )


# ─── Per-test fixtures ───────────────────────────────────────────────────────

@pytest.fixture
async def ha(ha_token: str, doorman_installed):
    """Authenticated HaClient for REST calls. Doorman is guaranteed installed."""
    async with HaClient(HA_URL, ha_token) as client:
        yield client


@pytest.fixture
async def ws(ha_token: str, doorman_installed):
    """Authenticated HaWebSocket. Doorman is guaranteed installed."""
    async with HaWebSocket(HA_URL, ha_token) as client:
        yield client


@pytest.fixture
def mock_2n() -> Mock2nAdmin:
    """Admin client for the mock 2N server."""
    return Mock2nAdmin(MOCK_2N_URL)


@pytest.fixture
def mock_2n_follower() -> Mock2nAdmin:
    """Admin client for the follower mock 2N server."""
    return Mock2nAdmin(MOCK_2N_FOLLOWER_URL)


@pytest.fixture(autouse=True)
def socket_enabled():
    """Re-enable sockets for integration tests.

    pytest-homeassistant-custom-component installs pytest-socket which blocks
    all network access by default. Integration tests need real HTTP calls.
    """
    try:
        import pytest_socket
        pytest_socket.enable_socket()
    except (ImportError, AttributeError):
        pass
    yield


@pytest.fixture(autouse=True)
async def reset_mock_2n(mock_2n: Mock2nAdmin):
    """Reset mock server state before each test so tests are independent."""
    await mock_2n.reset()
    yield
