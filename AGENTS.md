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
  __init__.py          — async_setup: domain-level registration (store, WS, services,
                         static path, panel — see "Domain-level registration" below);
                         async_setup_entry: per-device coordinator, platforms, tasks
                         (services incl. doorman.hangup_calls and doorman.resync_log_history)
  api_client.py        — TwoNApiClient: async HTTP wrapper around the 2N local REST API
                         (directory, switches, log, log history, and call control)
  config_flow.py       — UI config flow (host / username / password / SSL options)
  coordinator.py       — DataUpdateCoordinator: polls users, switches, log; fires bus
                         events (incl. the doorbell); runs the access-log backfill
  storage.py           — DoormanStore: persists UUID↔HA-user links, notify targets,
                         and per-entry notification settings
                         AccessLogStore: durable per-entry access-log history
  notifications.py     — Listens for doorman_access bus events, dispatches notify.* calls
  ios_sounds.py        — Static catalog of iOS Companion notification sounds for the panel
  websocket.py         — 12 WebSocket commands exposed to the frontend panel
  sensor.py            — User count sensor
  switch.py            — Relay switches (one entity per 2N relay)
  event.py             — Access event entity
  frontend/panel.js    — Vanilla JS sidebar panel (no build step)
  const.py             — All constants
```

## Key design decisions

### Domain-level registration happens in `async_setup`, before any network I/O
The shared `DoormanStore`, the WebSocket commands, the `doorman.*` services,
the frontend static route and the sidebar panel are all registered in
`async_setup` — once per HA run — and are **never** torn down. Only per-entry
resources (coordinator, platforms, background tasks, repair issue) live in
`async_setup_entry` / `async_unload_entry`.

They used to be registered at the end of `async_setup_entry`, which is reached
only *after* `coordinator.async_init_device_info()`. That call raises
`ConfigEntryNotReady` when the intercom is unreachable, so an intercom that was
unplugged, rebooting or on a flapping network at HA startup produced **no
Doorman sidebar entry at all**, indefinitely (HA retries with backoff up to
~80 s per attempt) — exactly when the user needs the panel to tell them the
device cannot be reached. The panel already degrades gracefully on its own:
`ws_list_devices` returns `{"devices": []}` and `panel.js`'s `_loadDevices`
renders an "unavailable" state.

The old domain-level teardown in `async_unload_entry` (`if not
hass.data[DOMAIN]: …`) is gone for the same reason. Reloading the only entry —
what an options change and a reauth both do — ran it, so mid-reload the sidebar
entry, the store and every `doorman.*` service disappeared and came back.

Consequences to keep in mind when adding code:
- **WS handlers and service handlers can run with zero loaded entries.**
  `_coordinator()` returns `None` and handlers must answer
  `not_configured`; `_resolve_coordinator()` raises the
  `no_devices_configured` `ServiceValidationError`. Never assume at least one
  entry exists.
- **The store outlives every entry**, so it is created and loaded in
  `async_setup`. Anything reading `hass.data[f"{DOMAIN}_store"]` (WS handlers,
  `notifications.py`, `delete_user`) can rely on it existing whenever the
  component is loaded.
- Panel registration failure is caught and logged: a missing sidebar entry must
  never stop entities, events and services from working.

Note on the static route: re-registering it is *not* an error. On aiohttp 3.13.3
(current HA) `HomeAssistantHTTP._async_register_static_paths` has no dedupe and
static resources are unnamed, so a repeat registration silently appends another
router resource — it does **not** raise `ValueError("Duplicate")`, which an
earlier comment here and a `contextlib.suppress(ValueError)` both claimed. Both
are gone; registering once per run makes the question moot.

### Updating the integration requires an HA restart
This is intrinsic, not a bug to fix: HA imports `custom_components` once via
`importlib.import_module` and has no module-unloading path, and live entity
instances and registered handlers hold references to the old classes. Reloading
the config entry re-runs the *old* code. README's "Updating" section is the
user-facing statement of this; don't "fix" it with reload tricks.

The frontend has a matching constraint. `panel.js` is served with a
`?v={manifest version}` cache-buster and HA re-imports module panels per URL, so
after an update the module re-executes **in the same document**, where the
previous version's custom elements are already defined. Therefore:
- Every `customElements.define()` goes through the guarded `define()` helper in
  `panel.js`. An unguarded call throws `NotSupportedError` and the panel then
  fails to render at all — a hard failure instead of a soft one. A unit test
  asserts there are no unguarded call sites.
- `async_register_panel` passes `config={"version": …}`, which arrives as
  `panel.config` in the panel element. `panel.js` compares it against its
  hardcoded `PANEL_VERSION` and shows a "reload this page to finish updating"
  banner on mismatch. **Bump `PANEL_VERSION` together with
  `manifest.json`'s version** — a unit test asserts the two match, otherwise
  every user would see the banner permanently.
- Version-namespaced element names would make an update take effect without a
  page reload. That is a larger, riskier change; deliberately not done.

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
would spam every notify target with stale door events after each restart. It
covers the synthetic `DoorbellPressed` event too: that is fired from inside
`_fire_new_access_events`, so it is on the live path only and a backfilled
`KeyPressed` can never ring anyone's phone.

Dedupe key is `id@utcTime`, not `id` alone: the 2N event `id` is a uint32 that
restarts at 1 after a device reboot, so a post-reboot event reusing an old id
is still recorded, while a repeated backfill (or backfill overlapping the live
feed) collapses to one row.

### Notification targets stored per 2N UUID
`DoormanStore` persists `notification_targets: {two_n_uuid: ["notify.service", …]}`.
The keys are 2N UUIDs, not HA user IDs, because a 2N user may exist without
being linked to any HA account. The `notifications.py` module reads these
targets when a `UserAuthenticated` event fires.

### Notification settings live in DoormanStore, not entry.options
The six per-flow notification settings (`access_sound_ios`,
`access_channel_android`, `doorbell_sound_ios`, `doorbell_channel_android`,
`doorbell_key_code`, `doorbell_targets`) are persisted in `DoormanStore`
under `notification_settings`, keyed by config `entry_id`. Two reasons, both
load-bearing:

1. **The options flow would wipe them.** `DoormanOptionsFlow` renders only
   `poll_interval` and finishes with `async_create_entry(data=user_input)`,
   which *replaces* the whole options dict. Anything else stored there is
   destroyed the first time a user opens Configure and presses Submit.
2. **Writing options reloads the entry.** `add_update_listener` →
   `async_reload` tears down the 2N log subscription, and per the section
   above a fresh subscription starts empty with no watermark, so events in
   the reload window are lost. (The panel, store and services survive a
   reload now — see "Domain-level registration" — but the lost events do not.)

Because nothing reloads, readers must not cache: `coordinator.py` re-reads
the doorbell key from the store on every log batch and `notifications.py`
reads sounds/channels per event, so a panel save applies to the next event.
Keying by `entry_id` matters — `DoormanStore` is one shared instance across
all entries, so an unkeyed dict would merge two doors' settings.

An empty `doorbell_key_code` means "this device has no doorbell button" and
disables the flow; to restore the default, send `"%1"` explicitly. Only the
2N `%N` quick-dial form is accepted — a bare digit would make every PIN
keystroke ring the doorbell.

### The reauth flow reloads the entry exactly once
`async_update_entry` fires the entry's update listeners on **any** change, not
just an options change, and `__init__.py` registers
`_async_reload_on_options_update`. HA runs those listeners eagerly, so by the
time `async_update_entry` returns, the reload it triggered has already moved the
entry to `UNLOAD_IN_PROGRESS`. `async_step_reauth_confirm` therefore captures
`entry.state` *before* the update and calls `async_reload` itself only when the
listener cannot have run: nothing changed (same credentials re-entered), or the
entry was not loaded — the usual case, since credentials rejected at startup
raise `ConfigEntryAuthFailed` before `add_update_listener` is reached. Calling
both unconditionally gave one reauth two full teardown/rebuild cycles; dropping
the explicit call entirely would leave a `SETUP_ERROR` entry unrecovered.
`OptionsFlowWithReload` / `async_update_reload_and_abort` express this natively
but postdate the `homeassistant: 2024.1.0` minimum pinned in `hacs.json`.

### WebSocket API surface
All frontend↔backend communication goes through the 12 WS commands in
`websocket.py`. Don't add HA services for things that only the panel needs;
use WS commands instead. Services are for HA automations / scripts.
Give every command a real voluptuous schema (not just types) and test it
through the `hass_ws_client` fixture — calling a handler directly bypasses
schema validation entirely. Tests that need `hass_ws_client` must be marked
`@pytest.mark.real_http` so the conftest sets up the genuine `http`
component instead of the MagicMock.

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

### Integration test infrastructure: Podman locally, Docker Compose in CI
The Compose integration tests in `tests/integration/` run with `podman compose`
/ `podman-compose` on the developer's Mac and with **Docker Compose v2** in
`.github/workflows/integration.yml` (`docker compose`, pre-installed on
`ubuntu-latest`). The mock 2N server is a lightweight aiohttp server that mimics
the real device's REST API.

Keep `docker-compose.yml` portable — no runtime-specific keys — so the same file
serves both. Three rules exist because breaking them cost a multi-day CI outage
in August 2026, when the job hung for ~19.5 minutes and was killed by its own
`timeout-minutes` with no logs:

1. **No `depends_on: condition: service_healthy`.** Container healthchecks are
   run by the container runtime, and under rootless Podman on CI runners they
   are implemented as systemd transient timers that never fire without a user
   systemd session — so the dependent service waits forever. Readiness is gated
   explicitly instead: `curl` polling in the workflow (with `timeout`) and the
   session fixtures in `tests/integration/conftest.py`. Both have their own
   deadlines; the compose-level gate was pure redundancy with an unbounded wait.
   The `healthcheck:` blocks themselves stay — they make `compose ps` informative.
2. **Pin the runner dependencies.** `tests/integration/requirements.txt` pins
   `aiohttp`, `pytest`, `pytest-asyncio` and `websockets` exactly. The unpinned
   `pip install` that preceded it is what let CI change under the project's feet
   on an unchanged tree. (Unit tests keep their own `requirements_test.txt`;
   don't merge the two — that stack pins its own pytest via
   `pytest-homeassistant-custom-component`.)
3. **Every long workflow step carries `timeout-minutes`, and the log-dump steps
   use `if: always()`, not `if: failure()`.** A job cancelled by its timeout
   does not run `failure()` steps, which is why the hung runs produced no
   container logs whatsoever.

Nothing in the tests or helpers depends on container *names*, so the
`integration_mock-2n_1` (podman-compose) vs `integration-mock-2n-1`
(Compose v2) difference is immaterial. `MOCK_2N_HOST=mock-2n` is the compose
*service* name, which both runtimes publish as a network alias. Keep it that way.

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
  `__init__.py`, so a release is enough — no manual busting needed. Do bump
  `PANEL_VERSION` in `panel.js` in the same commit as `manifest.json`'s
  version (see "Updating the integration requires an HA restart").
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
Add it to `DoormanStore` in `storage.py` — not to `entry.options` (see
"Notification settings live in DoormanStore" above). Update `_empty_data()`
to include the new key; `async_load` layers stored data over it, so existing
`.storage` files pick up new keys with safe defaults automatically. Adding a
key is *not* a breaking schema change and does not need a `STORAGE_VERSION`
bump — only bump it when existing values change shape or meaning.

Settings that belong to a specific 2N device must be keyed by config
`entry_id`: the store is a single shared instance across all entries.
