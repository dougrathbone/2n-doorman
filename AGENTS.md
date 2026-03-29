# Doorman — AI Agent Guide

This document captures design decisions, architectural context, and conventions
for AI agents (Claude Code and similar) working in this repository.

## Project overview

Doorman is a Home Assistant custom integration for managing 2N IP intercom
users, credentials, and access control. It fills a gap: no existing HA
integration touches `/api/dir/*` (the 2N user directory). Everything in this
repo was built to cover that gap.

## Architecture summary

```
custom_components/doorman/
  __init__.py          — entry setup, static file serving, panel registration, services
  api_client.py        — TwoNApiClient: async HTTP wrapper around the 2N local REST API
  config_flow.py       — UI config flow (host / username / password / SSL options)
  coordinator.py       — DataUpdateCoordinator: polls users, switches, log; fires bus events
  storage.py           — DoormanStore: persists UUID↔HA-user links and notify targets
  notifications.py     — Listens for doorman_access bus events, dispatches notify.* calls
  websocket.py         — 9 WebSocket commands exposed to the frontend panel
  sensor.py            — User count sensor
  switch.py            — Relay switches (one entity per 2N relay)
  event.py             — Access event entity
  frontend/panel.js    — Vanilla JS sidebar panel (no build step)
  const.py             — All constants
```

## Key design decisions

### No build step for the frontend
`panel.js` uses plain vanilla JS custom elements and Shadow DOM. There is
deliberately no bundler, no TypeScript, no npm. This keeps the integration
self-contained and easy to install via HACS without a CI build artifact for
the JS. If the frontend grows significantly, consider a build step, but for
now keep it simple.

### TwoNApiClient written from scratch
The `py2n` library only covers relay/camera/event operations; it has no
`/api/dir/*` support. We own the full HTTP layer using `aiohttp.BasicAuth`
directly. Don't replace this with py2n unless it gains full directory support.

### Entity ID stability
`DoormanRelay` always uses `f"Doorman Relay {self._switch_id}"` as the entity
name — never the 2N device relay name. This produces predictable entity IDs
(`switch.doorman_relay_1`) even when the relay is renamed on the intercom.
The device name is stored in `extra_state_attributes["device_name"]`. Don't
change this without updating all tests and any automations people may have
written.

### First-poll event suppression
`DoormanCoordinator._fire_new_access_events` records the latest log event ID
on the first poll but does **not** fire bus events for historical entries.
Only events that appear on a subsequent poll (i.e., newer than the watermark)
fire `doorman_access` events. This prevents a flood of stale notifications
on every HA restart.

### Notification targets stored per 2N UUID
`DoormanStore` persists `notification_targets: {two_n_uuid: ["notify.service", …]}`.
The keys are 2N UUIDs, not HA user IDs, because a 2N user may exist without
being linked to any HA account. The `notifications.py` module reads these
targets when a `UserAuthenticated` event fires.

### WebSocket API surface
All frontend↔backend communication goes through the 9 WS commands in
`websocket.py`. Don't add HA services for things that only the panel needs;
use WS commands instead. Services are for HA automations / scripts.

### Dependency mocking in unit tests
HA's `frontend`, `panel_custom`, and `http` components require heavy optional
dependencies (`hass-frontend`, a live HTTP server). Unit tests use
`mock_component(hass, "frontend")` etc. in the `mock_frontend_setup` autouse
fixture to mark them as already loaded. The actual HTTP serving is validated
only in the Docker-based integration tests.

### Custom component path injection
`pytest-homeassistant-custom-component`'s testing config has its own
`custom_components/__init__.py` (regular package) that shadows ours. The
conftest.py injects our `custom_components/` directory into
`custom_components.__path__` at import time so HA's loader finds doorman.
Don't remove this or HA will silently fail to load the integration in tests.

### Integration test infrastructure uses Podman (not Docker)
The Docker Compose integration tests in `tests/integration/` are designed to
run with `podman compose` (or `docker compose`). On the developer's Mac,
Podman is used. CI uses whatever is available. The mock 2N server is a
lightweight aiohttp server that mimics the real device's REST API.

### No Claude attribution in commits
Per the project's `CLAUDE.md`: never add "Co-Authored-By: Claude" or similar
footers to commit messages. Keep commit messages focused on technical changes.

## Conventions

- **Python**: 3.12+ target, ruff for linting (`pyproject.toml`). Run
  `ruff check --fix` before committing.
- **Tests**: `pytest tests/ --ignore=tests/integration` for unit tests.
  Integration tests require Docker/Podman and a running HA instance.
- **Releases**: tag `vX.Y.Z` → GitHub Actions zips `custom_components/doorman/`
  and creates a GitHub Release. HACS installs from the release zip.
- **Frontend changes**: edit `frontend/panel.js` directly; no build step.
  Reload the HA frontend after changes (or bump cache-busting if needed).
- **Storage keys**: `STORAGE_KEY` and `STORAGE_VERSION` are defined in
  `const.py`. Bump `STORAGE_VERSION` when the stored schema changes
  in a breaking way.

## Common tasks

### Add a new WebSocket command
1. Write the handler function in `websocket.py` with `@websocket_api.websocket_command`
2. Register it in `async_setup_websocket`
3. Call it from `frontend/panel.js` via `hass.callWS({type: "doorman/your_command"})`

### Add a new 2N API call
Add a method to `TwoNApiClient` in `api_client.py`. Use `self._get` or
`self._post` helpers. Raise `DoormanApiError` / `DoormanAuthError` as
appropriate.

### Add a new persistent setting
Add it to `DoormanStore` in `storage.py`. Update `_EMPTY` to include the
new key so existing `.storage` files get it on migration. If the schema
changes, bump `STORAGE_VERSION`.
