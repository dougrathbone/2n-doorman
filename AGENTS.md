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
                         (directory, switches, log, and call control)
  config_flow.py       — UI config flow (host / username / password / SSL options)
  coordinator.py       — DataUpdateCoordinator: polls users, switches, log; fires bus events
  storage.py           — DoormanStore: persists UUID↔HA-user links and notify targets
                         AccessLogStore: durable per-entry access-log history
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
written. All Doorman entities also set `_attr_has_entity_name = False` and pin
`self.entity_id` explicitly in `__init__`: with `device_info` present, HA
2026.7+ otherwise generates device-name-prefixed entity IDs
(`switch.2n_ip_vario_doorman_relay_1`) for new installs.

### Log events via long-poll subscription
`DoormanCoordinator` runs a background listener that subscribes to the device
log (`/api/log/subscribe`) and long-polls `/api/log/pull` with a 20 s
server-side timeout. The live subscription deliberately sends **no `include`
parameter**, so the device default (`include=new`) applies: its queue starts
empty and only events that occur while the subscription is alive are
delivered. There is deliberately no client-side watermark on this path.
`doorman_access` bus events carry the originating `entry_id`, and `utcTime` is
passed through as epoch seconds (the panel converts for display).

### The access log is persisted, and backfill must never notify
The access log is a durable historical record, not a view of the current
session. Two mechanisms keep it complete:

1. **Persistence** — `AccessLogStore` (in `storage.py`) writes each entry's
   history to its own `Store` file, `doorman.access_log.<entry_id>`. It is
   separate from `DoormanStore` (`doorman.storage`) on purpose: that file
   holds small config-ish maps and is rewritten in full on every save, so
   appending log events to it would rewrite user links and notification
   targets on every door open. Events are stored oldest-first (the order
   `panel.js` expects), deduplicated, capped at `MAX_STORED_LOG_EVENTS`
   (oldest trimmed first) and written through `Store.async_delay_save`, so a
   burst of events from one `pull_log` costs a single disk write.
   `async_shutdown` flushes any pending write, because HA only auto-flushes
   delayed saves on a clean stop, not on a config entry reload.
2. **Backfill** — `DoormanCoordinator._async_run_backfill` calls
   `TwoNApiClient.fetch_log_history`, which creates its **own throwaway
   subscription** with `include=-<seconds>` (falling back to `include=all`),
   drains it, and unsubscribes. It never touches
   `_log_subscription_id`: pulling from the live listener's subscription would
   consume events the listener must deliver. Any device error returns an empty
   list, so firmware that doesn't support the parameter behaves exactly as it
   did before backfill existed.

**Backfill runs as a background task, never on the setup path.**
`async_setup_entry` awaits `async_load_access_log()` (a local disk read — the
panel needs the persisted history immediately) but only *starts*
`coordinator.start_backfill()`, right after `start_log_listener()`, via
`hass.async_create_background_task`. Awaiting it inline could stall
config-entry setup for minutes against an unresponsive intercom: HA logs
slow-setup warnings and the integration sits unavailable the whole time.
Starting the live listener first means no event that happens during the
backfill can slip through the gap; overlap is collapsed by the store's dedupe.

The request budget is deliberately tight: `request_timeout=10` per pull and
`LOG_BACKFILL_MAX_PULLS = 5`, so a worst-case run is ~50 s. **Consequence:** at
128 events per pull a single backfill can retrieve at most 640 events (it was
1280 under the old 10-pull budget). The 7-day `LOG_BACKFILL_SECONDS` window is
unchanged; a device holding more than 640 events inside that window is only
partially backfilled.

`async_shutdown` cancels the backfill task **before** flushing `log_store`, and
sets `_closed`, which `_async_run_backfill` checks immediately before
`add_events`. Both are needed: the cancel stops the run, and the flag
guarantees that a run which somehow returns late cannot re-dirty a store that
has already been flushed — whose debounced write would otherwise re-create the
history file `async_remove_entry` just deleted.

`doorman.resync_log_history` re-runs the same code path on demand (see
"Services" below), so everything above applies to it too.

**Backfilled events must never fire `doorman_access` bus events or update
`_last_access`.** Only the live listener calls `_fire_new_access_events`;
backfill writes straight to the store. This is the same guarantee the old
no-watermark design provided — replaying history through the notification path
would spam every notify target with stale door events after each restart.

Dedupe key is `id@utcTime`, not `id` alone: the 2N event `id` is a uint32 that
restarts at 1 after a device reboot, so a post-reboot event reusing an old id
is still recorded, while a repeated backfill (or backfill overlapping the live
feed) collapses to one row.

### Notification targets stored per 2N UUID
`DoormanStore` persists `notification_targets: {two_n_uuid: ["notify.service", …]}`.
The keys are 2N UUIDs, not HA user IDs, because a 2N user may exist without
being linked to any HA account. The `notifications.py` module reads these
targets when a `UserAuthenticated` event fires.

### WebSocket API surface
All frontend↔backend communication goes through the 9 WS commands in
`websocket.py`. Don't add HA services for things that only the panel needs;
use WS commands instead. Services are for HA automations / scripts.

### `doorman.resync_log_history`
A service (not a WS command — a manual resync is legitimately automatable)
that re-runs the access-log backfill on demand. It exists for two reasons:
the `include=-N` / `include=all` parameters come from the 2N HTTP API docs and
have **never been confirmed against real firmware**, so the maintainer needs a
way to exercise them without restarting HA; and users whose first backfill came
up empty need a retry.

It logs its outcome at INFO, deliberately distinguishing three cases so a bug
report is unambiguous:
- `fetched == 0` — the device served *no* history at all. Most likely the
  firmware rejects the `include` parameter, i.e. the unverified assumption is
  wrong on that device.
- `fetched > 0, added == 0` — history retrieval works, there was just nothing
  new. Before this service existed these two looked identical from the log.
- `added > 0` — how many rows were actually merged.

Concurrency: `_ensure_backfill_task` reuses the in-flight
`_backfill_task` and `async_resync_access_log` awaits it under
`asyncio.shield`, so hammering the service — or resyncing while the startup
backfill is still running — joins one run instead of opening a second history
subscription on the device. It targets a single entry via the standard optional
`device` (config entry ID) field, so only that entry's `AccessLogStore` is
touched.

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
  `panel.js` is cache-busted automatically with `?v={manifest version}` in
  `__init__.py`, so a release is enough — no manual busting needed.
- **Storage keys**: `STORAGE_KEY`/`STORAGE_VERSION` (shared config store) and
  `LOG_STORAGE_KEY`/`LOG_STORAGE_VERSION` (per-entry access log) are defined
  in `const.py`. Bump the matching version when a stored schema changes in a
  breaking way.

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
