# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Doorman is a Home Assistant custom integration for managing 2N IP intercom users, credentials, and access control via the local `/api/dir/*` REST API. Distributed through HACS. See `AGENTS.md` for detailed architecture and design decisions.

## Commands

```bash
# Lint
ruff check custom_components/ tests/
ruff check --fix custom_components/ tests/

# Unit tests (excludes integration tests that need Docker/Podman)
pytest tests/ --ignore=tests/integration -v

# Single test file
pytest tests/test_coordinator.py -v

# Single test
pytest tests/test_coordinator.py::test_coordinator_update -v

# Integration tests (requires podman-compose or docker-compose running)
podman-compose -f tests/integration/docker-compose.yml up -d --wait
pytest tests/integration/ -v
podman-compose -f tests/integration/docker-compose.yml down
```

## Key rules

- **No Claude attribution in commits.** Never add "Co-Authored-By: Claude" or similar footers.
- **No frontend build step.** `frontend/panel.js` is vanilla JS with Shadow DOM. No bundler, no TypeScript, no npm. Edit the file directly.
- **Entity ID stability.** Relay entities use `f"Doorman Relay {self._switch_id}"` as name, never the device-provided name. Do not change this.
- **WS commands for panel, services for automations.** Don't add HA services for things only the panel needs; use WebSocket commands. Services are for HA automations/scripts.
- **Run lint and tests before committing:** `ruff check --fix custom_components/ tests/ && pytest tests/ --ignore=tests/integration`

## Code style

- Python 3.12+, ruff linter (config in `pyproject.toml`)
- Line length: 100 (E501 ignored)
- Rules: E, F, W, I (isort), UP, B, SIM (SIM117 ignored)
- Tests may use private member access (SLF001) and late imports (E402)

## Architecture quick reference

- `api_client.py` — Custom aiohttp HTTP client with Digest auth for 2N REST API (don't replace with py2n)
- `coordinator.py` — Polls users/switches/log every 30s; fires `doorman_access` bus events (with first-poll suppression)
- `storage.py` — Persists UUID-to-HA-user links and notification targets (keyed by 2N UUID, not HA user ID)
- `websocket.py` — 9 WS commands for the frontend panel
- `notifications.py` — Dispatches `notify.*` calls on `doorman_access` events
- `const.py` — All constants including `DOMAIN = "doorman"`
- Entities: `sensor.py` (user count), `switch.py` (relays), `event.py` (access events) — all use CoordinatorEntity

## Testing patterns

- Unit tests mock `TwoNApiClient` via `mock_2n_client` fixture in `conftest.py`
- `mock_frontend_setup` autouse fixture mocks HA's frontend/http/panel_custom dependencies
- `conftest.py` injects `custom_components/` into the test path — do not remove this
- Integration tests use a real HA instance + mock 2N aiohttp server via Podman Compose
