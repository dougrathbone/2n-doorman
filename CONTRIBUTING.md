# Contributing to Doorman

Thanks for your interest. This guide covers the development workflow, code conventions, and how to test your changes.

---

## Getting started

```bash
git clone https://github.com/dougrathbone/2n-doorman
cd 2n-doorman
pip install -r requirements_test.txt
```

No build step is required. The sidebar panel is vanilla JS ES modules under `frontend/` — edit the relevant module directly.

---

## Project layout

```
custom_components/doorman/
  __init__.py          # Domain registration (store, WS, services, panel) + entry setup
  services.py          # Admin-gated doorman.* service handlers
  api_client.py        # HTTP + Digest-auth client for the 2N REST API
  coordinator.py       # DataUpdateCoordinator + log listener + backfill
  config_flow.py       # Setup wizard, reauth, reconfigure, options flow
  websocket.py         # HA WebSocket API handlers (called by the panel)
  notifications.py     # Push notification dispatch on access / doorbell events
  storage.py           # DoormanStore + AccessLogStore
  sanitize.py          # Credential redaction for list_users + log ingest
  helpers.py           # device_slug / pinned_entity_id / DeviceInfo
  diagnostics.py       # HA diagnostics support
  sensor.py            # User count / uptime sensors
  switch.py            # Relay switch entities
  event.py             # Access event entity
  camera.py            # JPEG snapshot camera
  binary_sensor.py     # Door contact, inputs, SIP registration
  button.py            # Device restart
  brand/               # Icons/logos for HA Brands Proxy (2026.3+)
  frontend/
    panel.js           # Shell — tabs, device picker, live subscribe
    helpers.js         # PANEL_VERSION, define(), BASE_CSS, shared helpers
    drawer.js          # Slide-over drawer
    users-tab.js
    log-tab.js
    device-tab.js
    notifications-tab.js
  translations/
    en.json            # UI strings
tests/
  conftest.py          # Shared fixtures and mock data
  test_*.py            # Unit tests (HA via pytest-homeassistant-custom-component)
  integration/         # Docker/Podman Compose suite against mock 2N + HA
```

---

## Running tests

```bash
# All unit tests
pytest tests/ --ignore=tests/integration -v

# With coverage report
pytest tests/ --ignore=tests/integration \
  --cov=custom_components/doorman \
  --cov-report=term-missing \
  --cov-fail-under=90

# Lint
ruff check custom_components/ tests/
```

The unit suite uses `pytest-homeassistant-custom-component`, which spins up a real (in-process) HA instance. Integration tests need Podman/Docker — see `tests/integration/`.

CI also runs a second unit-test job against the `hacs.json` HA floor (`requirements_test_floor.txt`).

---

## Code conventions

- **Python**: follow existing style; ruff is the linter (`pyproject.toml`). CI runs Python 3.14; `target-version` stays `py312` for the HA floor.
- **Async**: use `async_create_background_task` for long-running tasks that shouldn't block `async_block_till_done`
- **No secrets**: never commit real credentials, IPs, or device serials
- **Translations**: any new UI string goes in both `strings.json` and `translations/en.json`
- **Services**: service schemas live in `services.yaml`; human-readable labels in `translations/en.json`; register with `async_register_admin_service`
- **Frontend**: ES modules with Shadow DOM; every `customElements.define()` goes through the guarded `define()` helper in `helpers.js`. Prefer `createElement`/`textContent` over string interpolation into `innerHTML`
- **Versions**: bump `manifest.json` and `PANEL_VERSION` in `helpers.js` together before tagging a release

---

## Adding a new feature

1. Write the backend change (Python)
2. Add or update WebSocket handlers in `websocket.py` if the panel needs new data
3. Update the relevant panel module(s) under `frontend/` (ES modules, no bundler)
4. Add translations in `strings.json` and `translations/en.json`
5. Write tests — CI enforces ≥90% coverage on the current-HA job
6. Bump `manifest.json` + `PANEL_VERSION` only when preparing a release

---

## Submitting a pull request

- Target the `main` branch
- CI runs lint, unit tests (current HA + floor), coverage gate, HACS validation, and hassfest
- Fill in the PR template — especially the testing section
- One logical change per PR where practical
- Brand assets: ship icons/logos under both repo-root `brand/` (README/HACS)
  and `custom_components/doorman/brand/` (HA 2026.3+ local Brands Proxy API).
  Root `brand/logo.svg` is README/HACS-only; the Brands Proxy tree uses PNG.
  Do not open PRs against `home-assistant/brands` for custom integrations —
  that path is closed; local `brand/` is the source of truth.

---

## Working with a real device

The integration talks to the 2N HTTP API directly. To test against a real device:

1. Enable the HTTP API under **Services → HTTP API** on the device
2. Create an API user with **Directory**, **System (Control)**, and **Access Log** permissions
3. Set the host/username/password in HA and load the integration

The mock server in `tests/integration/` can simulate device responses without hardware — see that directory's README for setup.
